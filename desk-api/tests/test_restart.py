"""Tests for POST /api/workstations/{name}/restart."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workstations.reboot_instance")
@patch("app.routes.workstations.resolve_workstation")
def test_restart_workstation_success(mock_resolve: object, mock_reboot: object) -> None:
    mock_resolve.return_value = "i-abc123"
    mock_reboot.return_value = "i-abc123"

    resp = client.post("/api/workstations/main/restart")

    assert resp.status_code == 200
    assert resp.json() == {"instance_id": "i-abc123"}
    mock_resolve.assert_called_once_with(
        "main", region=None, profile=None, infra=False
    )
    mock_reboot.assert_called_once_with("i-abc123", region=None, profile=None)


@patch("app.routes.workstations.reboot_instance")
@patch("app.routes.workstations.resolve_workstation")
def test_restart_infra_instance(mock_resolve: object, mock_reboot: object) -> None:
    mock_resolve.return_value = "i-router1"
    mock_reboot.return_value = "i-router1"

    resp = client.post("/api/workstations/router/restart?infra=true")

    assert resp.status_code == 200
    assert resp.json() == {"instance_id": "i-router1"}
    mock_resolve.assert_called_once_with(
        "router", region=None, profile=None, infra=True
    )
    mock_reboot.assert_called_once_with("i-router1", region=None, profile=None)


@patch("app.routes.workstations.resolve_workstation")
def test_restart_not_found(mock_resolve: object) -> None:
    mock_resolve.side_effect = ValueError("Workstation not found: missing")

    resp = client.post("/api/workstations/missing/restart")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workstation not found: missing"
