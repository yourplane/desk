"""Tests for workflow routes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _workflow(workflow_id: str = "wf1", status: str = "active"):
    return SimpleNamespace(
        id=workflow_id,
        name="Deploy Flow",
        description="Deploy steps",
        created_at="2026-03-21T00:00:00Z",
        updated_at="2026-03-21T00:00:00Z",
        status=status,
        versions=[
            SimpleNamespace(
                version=1,
                created_at="2026-03-21T00:00:00Z",
                steps=[SimpleNamespace(action="start_workstation", target="main", script=None, user=None, timeout=None)],
            )
        ],
    )


def _run(run_id: str = "run1", status: str = "RUNNING"):
    return SimpleNamespace(
        id=run_id,
        workflow_id="wf1",
        workflow_version=1,
        status=status,
        created_at="2026-03-21T00:01:00Z",
        started_at="2026-03-21T00:01:00Z",
        finished_at=None,
        cancel_requested=False,
        current_step_index=0,
        step_results=[],
        error=None,
    )


@patch("app.routes.workflows.list_workflows")
def test_list_workflows(mock_list: MagicMock):
    mock_list.return_value = [_workflow()]
    response = client.get("/api/workflows")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "wf1"
    assert payload[0]["versions"][0]["version"] == 1


@patch("app.routes.workflows.create_workflow")
def test_create_workflow(mock_create: MagicMock):
    mock_create.return_value = _workflow()
    response = client.post(
        "/api/workflows",
        json={
            "name": "Deploy Flow",
            "description": "Deploy steps",
            "steps": [{"action": "start_workstation", "target": "main"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Deploy Flow"


def test_create_workflow_rejects_empty_name():
    response = client.post(
        "/api/workflows",
        json={"name": " ", "steps": [{"action": "start_workstation", "target": "main"}]},
    )
    assert response.status_code == 400


@patch("app.routes.workflows.get_workflow")
@patch("app.routes.workflows.create_run")
@patch("app.routes.workflows._RUNNER.submit")
def test_start_run(mock_submit: MagicMock, mock_create_run: MagicMock, mock_get_workflow: MagicMock):
    mock_get_workflow.return_value = _workflow()
    mock_create_run.return_value = _run()
    response = client.post("/api/workflows/wf1/runs", json={})
    assert response.status_code == 200
    assert response.json()["id"] == "run1"
    mock_submit.assert_called_once()


@patch("app.routes.workflows.get_run")
@patch("app.routes.workflows.update_run")
def test_cancel_run(mock_update: MagicMock, mock_get_run: MagicMock):
    mock_get_run.return_value = _run()
    updated = _run(status="CANCEL_REQUESTED")
    updated.cancel_requested = True
    mock_update.return_value = updated
    response = client.post("/api/workflow-runs/run1/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCEL_REQUESTED"

