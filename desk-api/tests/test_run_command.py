"""Tests for POST /api/workstations/{name}/run and GET /api/workstations/{name}/commands/{command_id}."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workstations.send_ssm_command", return_value="cmd-abc123")
@patch("app.routes.workstations.is_ssm_ready", return_value=True)
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_run_command_success(mock_resolve: MagicMock, mock_ssm_ready: MagicMock, mock_send: MagicMock) -> None:
    resp = client.post("/api/workstations/main/run", json={"script": "echo hello"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["command_id"] == "cmd-abc123"
    assert body["instance_id"] == "i-12345"
    mock_resolve.assert_called_once()
    mock_ssm_ready.assert_called_once_with("i-12345", region=None, profile=None)
    mock_send.assert_called_once_with(
        "i-12345", "echo hello", region=None, profile=None, timeout_seconds=3600,
    )


@patch("app.routes.workstations.send_ssm_command", return_value="cmd-abc123")
@patch("app.routes.workstations.is_ssm_ready", return_value=True)
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_run_command_with_user(mock_resolve: MagicMock, mock_ssm_ready: MagicMock, mock_send: MagicMock) -> None:
    resp = client.post(
        "/api/workstations/main/run",
        json={"script": "whoami", "user": "ubuntu"},
    )

    assert resp.status_code == 200
    call_args = mock_send.call_args
    assert "sudo -u ubuntu bash -c" in call_args[0][1]


@patch("app.routes.workstations.send_ssm_command", return_value="cmd-abc123")
@patch("app.routes.workstations.is_ssm_ready", return_value=True)
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_run_command_custom_timeout(mock_resolve: MagicMock, mock_ssm_ready: MagicMock, mock_send: MagicMock) -> None:
    resp = client.post(
        "/api/workstations/main/run",
        json={"script": "sleep 10", "timeout": 60},
    )

    assert resp.status_code == 200
    mock_send.assert_called_once_with(
        "i-12345", "sleep 10", region=None, profile=None, timeout_seconds=60,
    )


def test_run_command_empty_script() -> None:
    resp = client.post("/api/workstations/main/run", json={"script": "  "})

    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


@patch("app.routes.workstations.resolve_workstation", side_effect=ValueError("not found"))
def test_run_command_workstation_not_found(mock_resolve: MagicMock) -> None:
    resp = client.post("/api/workstations/noexist/run", json={"script": "echo hi"})

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@patch("app.routes.workstations.is_ssm_ready", return_value=False)
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_run_command_ssm_not_ready(mock_resolve: MagicMock, mock_ssm_ready: MagicMock) -> None:
    resp = client.post("/api/workstations/main/run", json={"script": "echo hi"})

    assert resp.status_code == 409
    assert "SSM-ready" in resp.json()["detail"]


@patch("app.routes.workstations.get_command_invocation")
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_get_command_status_success(mock_resolve: MagicMock, mock_get: MagicMock) -> None:
    mock_result = MagicMock()
    mock_result.command_id = "cmd-abc123"
    mock_result.status = "Success"
    mock_result.stdout = "hello\n"
    mock_result.stderr = ""
    mock_result.exit_code = 0
    mock_get.return_value = mock_result

    resp = client.get("/api/workstations/main/commands/cmd-abc123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["command_id"] == "cmd-abc123"
    assert body["status"] == "Success"
    assert body["stdout"] == "hello\n"
    assert body["stderr"] == ""
    assert body["exit_code"] == 0


@patch("app.routes.workstations.get_command_invocation")
@patch("app.routes.workstations.resolve_workstation", return_value="i-12345")
def test_get_command_status_in_progress(mock_resolve: MagicMock, mock_get: MagicMock) -> None:
    mock_result = MagicMock()
    mock_result.command_id = "cmd-abc123"
    mock_result.status = "InProgress"
    mock_result.stdout = "partial output"
    mock_result.stderr = ""
    mock_result.exit_code = None
    mock_get.return_value = mock_result

    resp = client.get("/api/workstations/main/commands/cmd-abc123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "InProgress"
    assert body["exit_code"] is None


@patch("app.routes.workstations.resolve_workstation", side_effect=ValueError("not found"))
def test_get_command_status_workstation_not_found(mock_resolve: MagicMock) -> None:
    resp = client.get("/api/workstations/noexist/commands/cmd-abc123")

    assert resp.status_code == 404
