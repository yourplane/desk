"""Tests for router-infra API routes."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.router_infra.get_router_infra_status")
def test_router_infra_status(mock_status: MagicMock) -> None:
    from desk.router_infra import DemandSource, RouterInfraStatus

    mock_status.return_value = RouterInfraStatus(
        phase="active",
        base_stack_status="UPDATE_COMPLETE",
        active_stack_status="CREATE_COMPLETE",
        asg_desired=1,
        asg_in_service=1,
        target_health="healthy",
        demand=True,
        active_stack_present=True,
        demand_sources=[DemandSource(name="main", ports=[5173], state="running")],
    )
    with patch("app.routes.router_infra.is_router_instance_ops_enabled", return_value=True):
        resp = client.get("/api/router-infra/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["phase"] == "active"
    assert body["friendly_label"] == "Running"
    assert body["target_health"] == "healthy"
    assert body["instance_ops_enabled"] is True
    assert body["demand_sources"] == [{"name": "main", "ports": [5173], "state": "running"}]


@patch("app.routes.router_infra.wake_router_infra")
def test_router_infra_wake(mock_wake: MagicMock) -> None:
    mock_wake.return_value = {"step": "create_active_stack"}
    resp = client.post("/api/router-infra/wake")
    assert resp.status_code == 200
    assert resp.json()["step"] == "create_active_stack"


@patch("app.routes.router_infra.sleep_router_infra")
def test_router_infra_sleep_force(mock_sleep: MagicMock) -> None:
    mock_sleep.return_value = {"step": "sleep"}
    resp = client.post("/api/router-infra/sleep", json={"force": True})
    assert resp.status_code == 200
    mock_sleep.assert_called_once()
    assert mock_sleep.call_args.kwargs.get("force") is True


@patch("app.routes.router_infra.sleep_router_infra")
def test_router_infra_sleep_conflict(mock_sleep: MagicMock) -> None:
    mock_sleep.side_effect = ValueError("still needed")
    resp = client.post("/api/router-infra/sleep", json={"force": False})
    assert resp.status_code == 409
