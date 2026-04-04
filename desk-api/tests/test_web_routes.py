"""Tests for web route API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.web_routes.resolve_workstation")
@patch("app.routes.web_routes.list_all_web_routes")
def test_list_all_web_routes(mock_list: MagicMock, mock_resolve: MagicMock) -> None:
    mock_list.return_value = {"ws1": [80, 443]}

    resp = client.get("/api/web-routes")

    assert resp.status_code == 200
    assert resp.json() == {"routes": {"ws1": [80, 443]}}
    mock_resolve.assert_not_called()


@patch("app.routes.web_routes.resolve_workstation")
@patch("app.routes.web_routes.get_ports")
def test_get_workstation_web_routes(mock_get: MagicMock, mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = "i-123"
    mock_get.return_value = [8080, 9090]

    resp = client.get("/api/workstations/my-ws/web-routes")

    assert resp.status_code == 200
    assert resp.json() == {"name": "my-ws", "ports": [8080, 9090]}
    mock_resolve.assert_called_once()
    mock_get.assert_called_once_with("my-ws")


@patch("app.routes.web_routes.resolve_workstation")
def test_get_workstation_not_found(mock_resolve: MagicMock) -> None:
    mock_resolve.side_effect = ValueError("Workstation 'nope' not found")

    resp = client.get("/api/workstations/nope/web-routes")

    assert resp.status_code == 404


@patch("app.routes.web_routes.resolve_workstation")
@patch("app.routes.web_routes.add_port")
def test_add_port(mock_add: MagicMock, mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = "i-123"
    mock_add.return_value = [80, 443]

    resp = client.post("/api/workstations/ws/web-routes", json={"port": 443})

    assert resp.status_code == 200
    assert resp.json() == {"name": "ws", "ports": [80, 443]}
    mock_add.assert_called_once_with("ws", 443)


@patch("app.routes.web_routes.resolve_workstation")
@patch("app.routes.web_routes.remove_port")
def test_remove_port(mock_remove: MagicMock, mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = "i-123"
    mock_remove.return_value = [80]

    resp = client.delete("/api/workstations/ws/web-routes/443")

    assert resp.status_code == 200
    assert resp.json() == {"name": "ws", "ports": [80]}
    mock_remove.assert_called_once_with("ws", 443)


@patch("app.routes.web_routes.resolve_workstation")
@patch("app.routes.web_routes.remove_port")
def test_remove_port_not_registered(mock_remove: MagicMock, mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = "i-123"
    mock_remove.side_effect = ValueError("Port 9999 is not registered for workstation 'ws'")

    resp = client.delete("/api/workstations/ws/web-routes/9999")

    assert resp.status_code == 404
