"""Workflow definition and execution routes."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from desk.aws import (
    get_command_invocation,
    is_ssm_ready,
    resolve_workstation,
    send_ssm_command,
    start_workstation,
    stop_instance,
    terminate_instance,
)
from desk.config import get_default_profile, get_default_region
from desk.workflows import (
    WorkflowRunStepResult,
    clone_workflow_version,
    create_run,
    create_workflow,
    create_workflow_version,
    get_run,
    get_workflow,
    list_runs,
    list_workflows,
    update_run,
    update_workflow,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workflows"])

_RUNNER = ThreadPoolExecutor(max_workers=5, thread_name_prefix="workflow-runner")
_RUN_LOCKS: dict[str, threading.Lock] = {}


def _serialize_workflow(workflow) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "status": workflow.status,
        "versions": [
            {
                "version": version.version,
                "created_at": version.created_at,
                "steps": [
                    {
                        "action": step.action,
                        "target": step.target,
                        **({"script": step.script} if step.script is not None else {}),
                        **({"user": step.user} if step.user is not None else {}),
                        **({"timeout": step.timeout} if step.timeout is not None else {}),
                    }
                    for step in version.steps
                ],
            }
            for version in workflow.versions
        ],
    }


def _serialize_run(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "cancel_requested": run.cancel_requested,
        "current_step_index": run.current_step_index,
        "step_results": [
            {
                "index": step.index,
                "action": step.action,
                "target": step.target,
                "status": step.status,
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "error": step.error,
            }
            for step in run.step_results
        ],
        "error": run.error,
    }


class WorkflowStepBody(BaseModel):
    action: str
    target: str
    script: str | None = None
    user: str | None = None
    timeout: int | None = Field(default=None, ge=1)


class CreateWorkflowBody(BaseModel):
    name: str
    description: str = ""
    steps: list[WorkflowStepBody]


class UpdateWorkflowBody(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class CreateVersionBody(BaseModel):
    steps: list[WorkflowStepBody]


class StartRunBody(BaseModel):
    workflow_version: int | None = None


def _region_profile() -> tuple[str, str | None]:
    return get_default_region(), get_default_profile()


def _shell_quote(s: str) -> str:
    escaped = s.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def _update_run_step(run_id: str, result: WorkflowRunStepResult) -> None:
    run = get_run(run_id)
    steps = [s for s in run.step_results if s.index != result.index]
    steps.append(result)
    steps.sort(key=lambda item: item.index)
    update_run(run_id, step_results=steps, current_step_index=result.index)


def _run_step(action: str, step: dict[str, Any], *, region: str, profile: str | None) -> None:
    target = step["target"]
    if action == "start_workstation":
        instance_id = resolve_workstation(target, region=region, profile=profile, states=["stopped"])
        start_workstation(instance_id, shutdown_after="4h", region=region, profile=profile)
        return
    if action == "stop_workstation":
        instance_id = resolve_workstation(target, region=region, profile=profile)
        stop_instance(instance_id, region=region, profile=profile)
        return
    if action == "kill_workstation":
        instance_id = resolve_workstation(
            target,
            region=region,
            profile=profile,
            states=["pending", "running", "stopping", "stopped"],
        )
        terminate_instance(instance_id, region=region, profile=profile)
        return
    if action == "run_command":
        script = (step.get("script") or "").strip()
        if not script:
            raise ValueError("run_command step requires script")
        timeout = int(step.get("timeout") or 3600)
        user = (step.get("user") or "").strip() or None
        instance_id = resolve_workstation(target, region=region, profile=profile)
        if not is_ssm_ready(instance_id, region=region, profile=profile):
            raise RuntimeError(f"Workstation {target} ({instance_id}) is not SSM-ready")
        command_script = script if not user else f"sudo -u {user} bash -c {_shell_quote(script)}"
        command_id = send_ssm_command(
            instance_id,
            command_script,
            region=region,
            profile=profile,
            timeout_seconds=timeout,
        )
        deadline = time.time() + timeout
        while True:
            result = get_command_invocation(command_id, instance_id, region=region, profile=profile)
            if result.status in {"Success", "Cancelled", "Cancelling"}:
                return
            if result.status in {"Failed", "TimedOut"}:
                raise RuntimeError(result.stderr or f"run_command ended with status {result.status}")
            if time.time() >= deadline:
                raise TimeoutError("run_command step timed out while polling invocation")
            time.sleep(1.0)
    raise ValueError(f"Unsupported workflow action '{action}'")


def _execute_run(run_id: str) -> None:
    lock = _RUN_LOCKS.setdefault(run_id, threading.Lock())
    with lock:
        run = get_run(run_id)
        workflow = get_workflow(run.workflow_id)
        version = next((v for v in workflow.versions if v.version == run.workflow_version), None)
        if version is None:
            update_run(run_id, status="FAILED", finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"), error="Workflow version not found")
            return
        region, profile = _region_profile()
        for idx, step in enumerate(version.steps, start=1):
            fresh = get_run(run_id)
            if fresh.cancel_requested:
                update_run(run_id, status="CANCELED", finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
                return
            step_result = WorkflowRunStepResult(
                index=idx,
                action=step.action,
                target=step.target,
                status="RUNNING",
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            _update_run_step(run_id, step_result)
            try:
                _run_step(
                    step.action,
                    {
                        "target": step.target,
                        "script": step.script,
                        "user": step.user,
                        "timeout": step.timeout,
                    },
                    region=region,
                    profile=profile,
                )
                step_result.status = "SUCCEEDED"
                step_result.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                _update_run_step(run_id, step_result)
            except Exception as e:
                step_result.status = "FAILED"
                step_result.error = str(e)
                step_result.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                _update_run_step(run_id, step_result)
                update_run(
                    run_id,
                    status="FAILED",
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    error=str(e),
                )
                return
        update_run(run_id, status="SUCCEEDED", finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _normalize_steps(steps: list[WorkflowStepBody]) -> list[dict[str, Any]]:
    if not steps:
        raise HTTPException(status_code=400, detail="Workflow must contain at least one step.")
    payload = []
    for idx, step in enumerate(steps, start=1):
        action = step.action.strip()
        target = step.target.strip()
        if not action:
            raise HTTPException(status_code=400, detail=f"Step {idx}: action must not be empty.")
        if not target:
            raise HTTPException(status_code=400, detail=f"Step {idx}: target must not be empty.")
        payload.append(
            {
                "action": action,
                "target": target,
                **({"script": step.script} if step.script is not None else {}),
                **({"user": step.user} if step.user is not None else {}),
                **({"timeout": step.timeout} if step.timeout is not None else {}),
            }
        )
    return payload


@router.get("/workflows")
def list_workflows_route():
    return [_serialize_workflow(workflow) for workflow in list_workflows()]


@router.post("/workflows")
def create_workflow_route(body: CreateWorkflowBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workflow name must not be empty.")
    workflow = create_workflow(name, body.description, _normalize_steps(body.steps))
    return _serialize_workflow(workflow)


@router.get("/workflows/{workflow_id}")
def get_workflow_route(workflow_id: str):
    try:
        workflow = get_workflow(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _serialize_workflow(workflow)


@router.patch("/workflows/{workflow_id}")
def update_workflow_route(workflow_id: str, body: UpdateWorkflowBody):
    fields: dict[str, Any] = {}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Workflow name must not be empty.")
        fields["name"] = body.name.strip()
    if body.description is not None:
        fields["description"] = body.description
    if body.status is not None:
        if body.status not in {"active", "archived"}:
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'archived'.")
        fields["status"] = body.status
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update.")
    try:
        workflow = update_workflow(workflow_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _serialize_workflow(workflow)


@router.post("/workflows/{workflow_id}/versions")
def create_workflow_version_route(workflow_id: str, body: CreateVersionBody):
    try:
        version = create_workflow_version(workflow_id, _normalize_steps(body.steps))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"version": version.version, "created_at": version.created_at}


@router.post("/workflows/{workflow_id}/versions/{version}/clone")
def clone_workflow_version_route(workflow_id: str, version: int):
    try:
        cloned = clone_workflow_version(workflow_id, version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"version": cloned.version, "created_at": cloned.created_at}


@router.post("/workflows/{workflow_id}/runs")
def start_run_route(workflow_id: str, body: StartRunBody):
    try:
        workflow = get_workflow(workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if workflow.status != "active":
        raise HTTPException(status_code=409, detail="Workflow is archived.")
    version_number = body.workflow_version or max(v.version for v in workflow.versions)
    if not any(v.version == version_number for v in workflow.versions):
        raise HTTPException(status_code=404, detail=f"Workflow version '{version_number}' not found")
    run = create_run(workflow_id, version_number)
    _RUNNER.submit(_execute_run, run.id)
    return _serialize_run(run)


@router.get("/workflow-runs")
def list_runs_route():
    return [_serialize_run(run) for run in list_runs()]


@router.get("/workflow-runs/{run_id}")
def get_run_route(run_id: str):
    try:
        run = get_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _serialize_run(run)


@router.post("/workflow-runs/{run_id}/cancel")
def cancel_run_route(run_id: str):
    try:
        run = get_run(run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if run.status in {"SUCCEEDED", "FAILED", "CANCELED"}:
        return _serialize_run(run)
    updated = update_run(run_id, cancel_requested=True, status="CANCEL_REQUESTED")
    return _serialize_run(updated)

