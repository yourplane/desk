"""Tests for POST /api/workstations/reap."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workstations.reconcile_router_infra")
@patch("app.routes.workstations.reap_overdue")
def test_reap_includes_router_infra_reconcile(mock_reap: MagicMock, mock_reconcile: MagicMock) -> None:
    mock_reap.return_value = []
    mock_reconcile.return_value = {"action": "sleep", "step": "sleep"}
    res = client.post("/api/workstations/reap")
    assert res.status_code == 200
    body = res.json()
    assert body["stopped"] == []
    assert body["router_infra"] == {"action": "sleep", "step": "sleep"}
    mock_reconcile.assert_called_once()
