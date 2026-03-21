"""Workflow routes backed by AWS Step Functions."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workflow"])

_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}


class WorkflowMethod(BaseModel):
    """Method metadata exposed to frontend workflow builder."""

    id: str
    name: str
    description: str
    input_schema: dict[str, Any]


class WorkflowStepBody(BaseModel):
    """A single workflow step."""

    method_id: Literal["workstations.run_command"]
    workstation: str = Field(min_length=1)
    script: str = Field(min_length=1)
    user: str | None = None
    timeout: int = 3600
    poll_interval_seconds: int = 2


class StartWorkflowBody(BaseModel):
    """Request body for workflow execution."""

    steps: list[WorkflowStepBody] = Field(min_length=1, max_length=100)
    name: str | None = None


def _sfn_client():
    # boto3 is available in Lambda runtime; import lazily for local tests.
    import boto3  # type: ignore

    return boto3.client("stepfunctions")


def _state_machine_arn() -> str:
    arn = os.getenv("WORKFLOW_STATE_MACHINE_ARN", "").strip()
    if not arn:
        raise HTTPException(
            status_code=503,
            detail="Workflow engine is not configured (missing WORKFLOW_STATE_MACHINE_ARN).",
        )
    return arn


def _execution_name(prefix: str | None = None) -> str:
    base = (prefix or "workflow").strip().replace(" ", "-")
    if not base:
        base = "workflow"
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{base[:40]}-{now}-{uuid4().hex[:8]}"


def _parse_json_or_raw(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


@router.get("/workflow/methods")
def list_workflow_methods() -> list[WorkflowMethod]:
    """Return available workflow step methods and schemas."""
    return [
        WorkflowMethod(
            id="workstations.run_command",
            name="Run command on workstation",
            description=(
                "Send a shell command to a workstation and poll until the command reaches "
                "a terminal SSM status."
            ),
            input_schema={
                "type": "object",
                "required": ["workstation", "script"],
                "properties": {
                    "workstation": {"type": "string", "minLength": 1},
                    "script": {"type": "string", "minLength": 1},
                    "user": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
                    "poll_interval_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 2,
                    },
                },
            },
        )
    ]


@router.post("/workflow/runs")
def start_workflow_run(body: StartWorkflowBody):
    """Start a workflow run in Step Functions."""
    arn = _state_machine_arn()
    client = _sfn_client()
    steps = [s.model_dump() for s in body.steps]
    # Step function only supports command execution steps for now.
    payload = {"steps": steps}
    try:
        response = client.start_execution(
            stateMachineArn=arn,
            name=_execution_name(body.name),
            input=json.dumps(payload),
        )
    except Exception as e:
        logger.exception("start_execution failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "execution_arn": response["executionArn"],
        "start_date": response["startDate"].isoformat(),
        "status": "RUNNING",
    }


@router.get("/workflow/runs/{execution_arn:path}")
def get_workflow_run(execution_arn: str):
    """Describe a workflow run and return parsed input/output when available."""
    client = _sfn_client()
    try:
        response = client.describe_execution(executionArn=execution_arn)
    except Exception as e:
        logger.exception("describe_execution failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    status = response["status"]
    return {
        "execution_arn": execution_arn,
        "state_machine_arn": response.get("stateMachineArn"),
        "status": status,
        "is_terminal": status in _TERMINAL_STATUSES,
        "start_date": response["startDate"].isoformat(),
        "stop_date": response.get("stopDate").isoformat() if response.get("stopDate") else None,
        "input": _parse_json_or_raw(response.get("input")),
        "output": _parse_json_or_raw(response.get("output")),
    }


@router.post("/workflow/runs/{execution_arn:path}/cancel")
def cancel_workflow_run(execution_arn: str):
    """Cancel a running workflow execution."""
    client = _sfn_client()
    try:
        client.stop_execution(
            executionArn=execution_arn,
            error="CancelledByUser",
            cause="Cancelled via Desk UI",
        )
    except Exception as e:
        logger.exception("stop_execution failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"execution_arn": execution_arn, "status": "ABORTED"}
