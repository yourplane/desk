"""Tests for GET /api/amis."""

from __future__ import annotations

from unittest.mock import patch

from desk.aws import AmiInfo
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.amis.list_amis")
def test_list_amis_success(mock_list_amis: object) -> None:
    """GET /api/amis returns desk-managed AMIs."""
    mock_list_amis.return_value = [
        AmiInfo(
            image_id="ami-new",
            name="default-desk-ami-20250701-120000",
            state="available",
            creation_date="2025-07-01T12:00:00.000Z",
            source_instance="i-src",
            build_status="tested",
        ),
    ]

    resp = client.get("/api/amis")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["image_id"] == "ami-new"
    assert data[0]["name"] == "default-desk-ami-20250701-120000"
    assert data[0]["build_status"] == "tested"
    mock_list_amis.assert_called_once_with(region=None, profile=None, managed_only=True)


@patch("app.routes.amis.list_amis")
def test_list_amis_empty(mock_list_amis: object) -> None:
    """GET /api/amis returns an empty list when no AMIs exist."""
    mock_list_amis.return_value = []

    resp = client.get("/api/amis")

    assert resp.status_code == 200
    assert resp.json() == []
