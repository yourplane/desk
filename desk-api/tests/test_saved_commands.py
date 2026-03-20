"""Tests for saved command CRUD API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _mock_cmd(id="abc123", name="Deploy", script="echo {{branch}}", description="", parameters=None):
    cmd = MagicMock()
    cmd.id = id
    cmd.name = name
    cmd.script = script
    cmd.description = description
    p = MagicMock()
    p.name = "branch"
    p.default = "main"
    cmd.parameters = parameters if parameters is not None else [p]
    return cmd


# ── list ────────────────────────────────────────────────────────────


@patch("app.routes.saved_commands.list_saved_commands")
def test_list_saved_commands_empty(mock_list: MagicMock) -> None:
    mock_list.return_value = []

    resp = client.get("/api/saved-commands")

    assert resp.status_code == 200
    assert resp.json() == []


@patch("app.routes.saved_commands.list_saved_commands")
def test_list_saved_commands_returns_all(mock_list: MagicMock) -> None:
    mock_list.return_value = [_mock_cmd()]

    resp = client.get("/api/saved-commands")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "abc123"
    assert body[0]["name"] == "Deploy"
    assert body[0]["parameters"][0]["name"] == "branch"
    assert body[0]["parameters"][0]["default"] == "main"


# ── create ──────────────────────────────────────────────────────────


@patch("app.routes.saved_commands.create_saved_command")
def test_create_saved_command_success(mock_create: MagicMock) -> None:
    mock_create.return_value = _mock_cmd()

    resp = client.post(
        "/api/saved-commands",
        json={
            "name": "Deploy",
            "script": "echo {{branch}}",
            "description": "Deploy a branch",
            "parameters": [{"name": "branch", "default": "main"}],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "abc123"
    assert body["name"] == "Deploy"
    mock_create.assert_called_once()


def test_create_saved_command_empty_name() -> None:
    resp = client.post(
        "/api/saved-commands",
        json={"name": "  ", "script": "echo hi"},
    )

    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_create_saved_command_empty_script() -> None:
    resp = client.post(
        "/api/saved-commands",
        json={"name": "Test", "script": "  "},
    )

    assert resp.status_code == 400
    assert "script" in resp.json()["detail"].lower()


# ── update ──────────────────────────────────────────────────────────


@patch("app.routes.saved_commands.update_saved_command")
def test_update_saved_command_success(mock_update: MagicMock) -> None:
    updated = _mock_cmd(name="Deploy v2")
    mock_update.return_value = updated

    resp = client.put(
        "/api/saved-commands/abc123",
        json={"name": "Deploy v2"},
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "Deploy v2"
    mock_update.assert_called_once_with("abc123", name="Deploy v2")


@patch("app.routes.saved_commands.update_saved_command")
def test_update_saved_command_not_found(mock_update: MagicMock) -> None:
    mock_update.side_effect = ValueError("not found")

    resp = client.put(
        "/api/saved-commands/nonexistent",
        json={"name": "X"},
    )

    assert resp.status_code == 404


def test_update_saved_command_no_fields() -> None:
    resp = client.put("/api/saved-commands/abc123", json={})

    assert resp.status_code == 400
    assert "no fields" in resp.json()["detail"].lower()


def test_update_saved_command_empty_name() -> None:
    resp = client.put(
        "/api/saved-commands/abc123",
        json={"name": "  "},
    )

    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


# ── delete ──────────────────────────────────────────────────────────


@patch("app.routes.saved_commands.delete_saved_command")
def test_delete_saved_command_success(mock_delete: MagicMock) -> None:
    resp = client.delete("/api/saved-commands/abc123")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    mock_delete.assert_called_once_with("abc123")


@patch("app.routes.saved_commands.delete_saved_command")
def test_delete_saved_command_not_found(mock_delete: MagicMock) -> None:
    mock_delete.side_effect = ValueError("not found")

    resp = client.delete("/api/saved-commands/nonexistent")

    assert resp.status_code == 404
