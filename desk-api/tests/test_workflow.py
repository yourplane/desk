"""Tests for workflow routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workflow._state_machine_arn", return_value="arn:aws:states:us-east-1:123:stateMachine:test")
@patch("app.routes.workflow._sfn_client")
def test_start_workflow_run(mock_client_factory: MagicMock, mock_arn: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.start_execution.return_value = {
        "executionArn": "arn:aws:states:us-east-1:123:execution:test:abc",
        "startDate": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }
    mock_client_factory.return_value = mock_client

    resp = client.post(
        "/api/workflow/runs",
        json={
            "steps": [
                {
                    "method_id": "workstations.run_command",
                    "workstation": "devbox",
                    "script": "echo hi",
                }
            ]
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "RUNNING"
    assert "execution_arn" in body


def test_list_workflow_methods() -> None:
    resp = client.get("/api/workflow/methods")
    assert resp.status_code == 200
    methods = resp.json()
    assert len(methods) == 1
    assert methods[0]["id"] == "workstations.run_command"


@patch("app.routes.workflow._sfn_client")
def test_get_workflow_run(mock_client_factory: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.describe_execution.return_value = {
        "status": "SUCCEEDED",
        "stateMachineArn": "arn:aws:states:us-east-1:123:stateMachine:test",
        "startDate": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "stopDate": datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        "input": '{"steps":[{"script":"echo hi"}]}',
        "output": '{"results":[{"status":"Success"}]}',
    }
    mock_client_factory.return_value = mock_client

    execution_arn = "arn:aws:states:us-east-1:123:execution:test:abc"
    resp = client.get(f"/api/workflow/runs/{execution_arn}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_terminal"] is True
    assert body["output"]["results"][0]["status"] == "Success"
