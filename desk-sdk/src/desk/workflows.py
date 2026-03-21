"""Workflow storage backed by S3."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from desk.config import get_default_profile, get_default_region

WORKFLOWS_KEY = "workflows.json"
WORKFLOW_RUNS_KEY = "workflow-runs.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class WorkflowStep:
    action: str
    target: str
    script: str | None = None
    user: str | None = None
    timeout: int | None = None


@dataclass
class WorkflowVersion:
    version: int
    created_at: str
    steps: list[WorkflowStep] = field(default_factory=list)


@dataclass
class Workflow:
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    status: str = "active"
    versions: list[WorkflowVersion] = field(default_factory=list)


@dataclass
class WorkflowRunStepResult:
    index: int
    action: str
    target: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


@dataclass
class WorkflowRun:
    id: str
    workflow_id: str
    workflow_version: int
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    current_step_index: int = 0
    step_results: list[WorkflowRunStepResult] = field(default_factory=list)
    error: str | None = None


def _get_data_bucket() -> str:
    bucket = os.environ.get("DESK_DATA_BUCKET")
    if not bucket:
        raise RuntimeError("DESK_DATA_BUCKET environment variable is not set")
    return bucket


def _s3_client():
    region = get_default_region()
    profile = get_default_profile()
    session = boto3.Session(region_name=region, profile_name=profile)
    return session.client("s3")


def _load_json(key: str) -> list[dict[str, Any]]:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(resp["Body"].read())
        return data if isinstance(data, list) else []
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return []
        raise


def _save_json(key: str, payload: list[dict[str, Any]]) -> None:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2),
        ContentType="application/json",
    )


def _step_from_dict(raw: dict[str, Any]) -> WorkflowStep:
    return WorkflowStep(
        action=raw["action"],
        target=raw["target"],
        script=raw.get("script"),
        user=raw.get("user"),
        timeout=raw.get("timeout"),
    )


def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
    result: dict[str, Any] = {"action": step.action, "target": step.target}
    if step.script is not None:
        result["script"] = step.script
    if step.user is not None:
        result["user"] = step.user
    if step.timeout is not None:
        result["timeout"] = step.timeout
    return result


def _workflow_from_dict(raw: dict[str, Any]) -> Workflow:
    versions = [
        WorkflowVersion(
            version=v["version"],
            created_at=v["created_at"],
            steps=[_step_from_dict(s) for s in v.get("steps", [])],
        )
        for v in raw.get("versions", [])
    ]
    return Workflow(
        id=raw["id"],
        name=raw["name"],
        description=raw.get("description", ""),
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        status=raw.get("status", "active"),
        versions=versions,
    )


def _workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "status": workflow.status,
        "versions": [
            {
                "version": v.version,
                "created_at": v.created_at,
                "steps": [_step_to_dict(s) for s in v.steps],
            }
            for v in workflow.versions
        ],
    }


def _run_step_from_dict(raw: dict[str, Any]) -> WorkflowRunStepResult:
    return WorkflowRunStepResult(
        index=raw["index"],
        action=raw["action"],
        target=raw["target"],
        status=raw["status"],
        started_at=raw.get("started_at"),
        finished_at=raw.get("finished_at"),
        error=raw.get("error"),
    )


def _run_step_to_dict(result: WorkflowRunStepResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "index": result.index,
        "action": result.action,
        "target": result.target,
        "status": result.status,
    }
    if result.started_at:
        payload["started_at"] = result.started_at
    if result.finished_at:
        payload["finished_at"] = result.finished_at
    if result.error:
        payload["error"] = result.error
    return payload


def _run_from_dict(raw: dict[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=raw["id"],
        workflow_id=raw["workflow_id"],
        workflow_version=raw["workflow_version"],
        status=raw["status"],
        created_at=raw["created_at"],
        started_at=raw.get("started_at"),
        finished_at=raw.get("finished_at"),
        cancel_requested=raw.get("cancel_requested", False),
        current_step_index=raw.get("current_step_index", 0),
        step_results=[_run_step_from_dict(s) for s in raw.get("step_results", [])],
        error=raw.get("error"),
    )


def _run_to_dict(run: WorkflowRun) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "workflow_version": run.workflow_version,
        "status": run.status,
        "created_at": run.created_at,
        "cancel_requested": run.cancel_requested,
        "current_step_index": run.current_step_index,
        "step_results": [_run_step_to_dict(s) for s in run.step_results],
    }
    if run.started_at:
        payload["started_at"] = run.started_at
    if run.finished_at:
        payload["finished_at"] = run.finished_at
    if run.error:
        payload["error"] = run.error
    return payload


def list_workflows() -> list[Workflow]:
    return [_workflow_from_dict(item) for item in _load_json(WORKFLOWS_KEY)]


def get_workflow(workflow_id: str) -> Workflow:
    for workflow in list_workflows():
        if workflow.id == workflow_id:
            return workflow
    raise ValueError(f"Workflow '{workflow_id}' not found")


def create_workflow(name: str, description: str, steps: list[dict[str, Any]]) -> Workflow:
    now = _utcnow()
    workflow = Workflow(
        id=secrets.token_hex(6),
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
        versions=[
            WorkflowVersion(
                version=1,
                created_at=now,
                steps=[_step_from_dict(s) for s in steps],
            )
        ],
    )
    workflows = list_workflows()
    workflows.append(workflow)
    _save_json(WORKFLOWS_KEY, [_workflow_to_dict(w) for w in workflows])
    return workflow


def update_workflow(workflow_id: str, *, name: str | None = None, description: str | None = None, status: str | None = None) -> Workflow:
    workflows = list_workflows()
    for idx, workflow in enumerate(workflows):
        if workflow.id != workflow_id:
            continue
        if name is not None:
            workflow.name = name
        if description is not None:
            workflow.description = description
        if status is not None:
            workflow.status = status
        workflow.updated_at = _utcnow()
        workflows[idx] = workflow
        _save_json(WORKFLOWS_KEY, [_workflow_to_dict(w) for w in workflows])
        return workflow
    raise ValueError(f"Workflow '{workflow_id}' not found")


def create_workflow_version(workflow_id: str, steps: list[dict[str, Any]]) -> WorkflowVersion:
    workflows = list_workflows()
    for idx, workflow in enumerate(workflows):
        if workflow.id != workflow_id:
            continue
        latest = max((v.version for v in workflow.versions), default=0)
        version = WorkflowVersion(
            version=latest + 1,
            created_at=_utcnow(),
            steps=[_step_from_dict(s) for s in steps],
        )
        workflow.versions.append(version)
        workflow.updated_at = _utcnow()
        workflows[idx] = workflow
        _save_json(WORKFLOWS_KEY, [_workflow_to_dict(w) for w in workflows])
        return version
    raise ValueError(f"Workflow '{workflow_id}' not found")


def clone_workflow_version(workflow_id: str, version_number: int) -> WorkflowVersion:
    workflow = get_workflow(workflow_id)
    source = next((v for v in workflow.versions if v.version == version_number), None)
    if source is None:
        raise ValueError(f"Workflow version '{version_number}' not found")
    return create_workflow_version(workflow_id, [_step_to_dict(s) for s in source.steps])


def list_runs() -> list[WorkflowRun]:
    runs = [_run_from_dict(item) for item in _load_json(WORKFLOW_RUNS_KEY)]
    return sorted(runs, key=lambda run: run.created_at, reverse=True)


def get_run(run_id: str) -> WorkflowRun:
    for run in list_runs():
        if run.id == run_id:
            return run
    raise ValueError(f"Workflow run '{run_id}' not found")


def create_run(workflow_id: str, workflow_version: int) -> WorkflowRun:
    run = WorkflowRun(
        id=secrets.token_hex(8),
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        status="RUNNING",
        created_at=_utcnow(),
        started_at=_utcnow(),
    )
    runs = list_runs()
    runs.append(run)
    _save_json(WORKFLOW_RUNS_KEY, [_run_to_dict(item) for item in runs])
    return run


def update_run(run_id: str, **fields: Any) -> WorkflowRun:
    runs = list_runs()
    for idx, run in enumerate(runs):
        if run.id != run_id:
            continue
        for key, value in fields.items():
            if hasattr(run, key):
                setattr(run, key, value)
        runs[idx] = run
        _save_json(WORKFLOW_RUNS_KEY, [_run_to_dict(item) for item in runs])
        return run
    raise ValueError(f"Workflow run '{run_id}' not found")

