"""Tests for the POST /api/workstations/{name}/auto-stop endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

_RESOLVE = "app.routes.workstations.resolve_workstation"
_SET_TAG = "app.routes.workstations.set_shutdown_tag"
_CLEAR_TAG = "app.routes.workstations.clear_shutdown_tag"
_COMPUTE = "app.routes.workstations.compute_shutdown_at"


@patch(_COMPUTE, return_value="2026-06-01T20:00:00Z")
@patch(_SET_TAG)
@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_with_duration(mock_resolve, mock_set, mock_compute):
    res = client.post("/api/workstations/main/auto-stop", json={"duration": "4h"})
    assert res.status_code == 200
    body = res.json()
    assert body["instance_id"] == "i-abc123"
    assert body["shutdown_at"] == "2026-06-01T20:00:00Z"
    mock_set.assert_called_once()


@patch(_SET_TAG)
@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_with_shutdown_at(mock_resolve, mock_set):
    res = client.post(
        "/api/workstations/main/auto-stop",
        json={"shutdown_at": "2026-06-01T17:30:00Z"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["instance_id"] == "i-abc123"
    assert body["shutdown_at"] == "2026-06-01T17:30:00Z"
    mock_set.assert_called_once()
    call_args = mock_set.call_args
    assert call_args[0][0] == "i-abc123"
    assert call_args[0][1] == "2026-06-01T17:30:00Z"


@patch(_SET_TAG)
@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_with_shutdown_at_offset_timezone(mock_resolve, mock_set):
    res = client.post(
        "/api/workstations/main/auto-stop",
        json={"shutdown_at": "2026-06-01T13:30:00-04:00"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["shutdown_at"] == "2026-06-01T17:30:00Z"


@patch(_CLEAR_TAG)
@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_clear(mock_resolve, mock_clear):
    res = client.post("/api/workstations/main/auto-stop", json={"clear": True})
    assert res.status_code == 200
    body = res.json()
    assert body["shutdown_cleared"] is True
    mock_clear.assert_called_once()


@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_both_duration_and_shutdown_at_rejected(mock_resolve):
    res = client.post(
        "/api/workstations/main/auto-stop",
        json={"duration": "4h", "shutdown_at": "2026-06-01T17:30:00Z"},
    )
    assert res.status_code == 400
    assert "not both" in res.json()["detail"].lower()


@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_invalid_shutdown_at(mock_resolve):
    res = client.post(
        "/api/workstations/main/auto-stop",
        json={"shutdown_at": "not-a-date"},
    )
    assert res.status_code == 400
    assert "Invalid shutdown_at" in res.json()["detail"]


@patch(_COMPUTE, return_value="2026-06-01T20:00:00Z")
@patch(_SET_TAG)
@patch(_RESOLVE, return_value="i-abc123")
def test_auto_stop_defaults_to_4h_when_no_fields(mock_resolve, mock_set, mock_compute):
    res = client.post("/api/workstations/main/auto-stop", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["shutdown_at"] == "2026-06-01T20:00:00Z"
