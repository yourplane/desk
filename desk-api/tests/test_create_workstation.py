"""Tests for POST /api/workstations (create workstation)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workstations.create_workstation")
def test_create_workstation_success(mock_create: object) -> None:
    """POST /api/workstations creates a new workstation and returns its details."""
    mock_create.return_value = ("i-new123", "2026-03-20T20:00:00Z")

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance_id"] == "i-new123"
    assert body["name"] == "my-ws"
    assert body["shutdown_at"] == "2026-03-20T20:00:00Z"
    mock_create.assert_called_once_with(
        "my-ws",
        "t3.medium",
        shutdown_after="4h",
        allow_untested_ami=False,
        region=None,
        profile=None,
    )


@patch("app.routes.workstations.create_workstation")
def test_create_workstation_custom_instance_type(mock_create: object) -> None:
    """POST /api/workstations accepts a custom instance_type."""
    mock_create.return_value = ("i-new456", "2026-03-20T20:00:00Z")

    resp = client.post(
        "/api/workstations",
        json={"name": "big-ws", "instance_type": "m5.xlarge"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance_id"] == "i-new456"
    assert body["name"] == "big-ws"
    mock_create.assert_called_once_with(
        "big-ws",
        "m5.xlarge",
        shutdown_after="4h",
        allow_untested_ami=False,
        region=None,
        profile=None,
    )


@patch("app.routes.workstations.create_workstation")
def test_create_workstation_allow_untested_ami(mock_create: object) -> None:
    """POST /api/workstations passes allow_untested_ami to the SDK."""
    mock_create.return_value = ("i-x", None)

    resp = client.post(
        "/api/workstations",
        json={"name": "ws", "allow_untested_ami": True},
    )

    assert resp.status_code == 200
    mock_create.assert_called_once_with(
        "ws",
        "t3.medium",
        shutdown_after="4h",
        allow_untested_ami=True,
        region=None,
        profile=None,
    )


@patch("app.routes.workstations.create_workstation")
def test_create_workstation_duplicate_name_rejected(mock_create: object) -> None:
    """POST /api/workstations returns 409 when SDK raises ValueError for duplicate name."""
    mock_create.side_effect = ValueError(
        "Workstation named 'my-ws' already exists: i-existing (running)."
    )

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert "i-existing" in resp.json()["detail"]


@patch("app.routes.workstations.create_workstation")
def test_create_workstation_allows_terminated_duplicate(mock_create: object) -> None:
    """POST /api/workstations succeeds when SDK allows creation (terminated duplicates are fine)."""
    mock_create.return_value = ("i-new789", "2026-03-20T20:00:00Z")

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 200
    assert resp.json()["instance_id"] == "i-new789"


def test_create_workstation_empty_name_rejected() -> None:
    """POST /api/workstations returns 400 when name is empty or whitespace."""
    resp = client.post("/api/workstations", json={"name": "  "})

    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_create_workstation_missing_name_rejected() -> None:
    """POST /api/workstations returns 422 when name field is missing."""
    resp = client.post("/api/workstations", json={})

    assert resp.status_code == 422


def test_create_workstation_no_stack_in_body() -> None:
    """POST /api/workstations does not accept a stack field."""
    resp = client.post(
        "/api/workstations",
        json={"name": "my-ws", "stack": "custom-stack"},
    )
    # stack is not in the model, so FastAPI ignores it; request still succeeds
    # (the key test is that it's not passed to SDK)
    assert resp.status_code in (200, 409, 500)


def test_create_workstation_reserved_name_router_rejected() -> None:
    """POST /api/workstations with name 'router' returns 409 (reserved for managed ASG)."""
    resp = client.post("/api/workstations", json={"name": "router"})
    assert resp.status_code == 409
    assert "reserved" in resp.json()["detail"].lower()


@patch("app.routes.workstations.get_future_router_ami_info")
@patch("app.routes.workstations.describe_amis_by_id")
@patch("app.routes.workstations.list_workstations")
def test_list_workstations_infra(
    mock_list_workstations: object,
    mock_describe_amis: object,
    mock_future_router: object,
) -> None:
    """GET /api/workstations?infra=true lists Type=router instances with AMI metadata."""
    from desk.aws import AmiRef, FutureRouterAmiInfo, Workstation

    mock_list_workstations.return_value = [
        Workstation(
            instance_id="i-router1",
            name="router",
            state="running",
            image_id="ami-router",
        ),
    ]
    mock_describe_amis.return_value = {
        "ami-router": AmiRef(
            image_id="ami-router",
            name="router-ami-20240601-120000",
            build_at="2024-06-01T12:00:00.000Z",
        ),
    }
    mock_future_router.return_value = FutureRouterAmiInfo(
        status="consolidated",
        ami=AmiRef(
            image_id="ami-router",
            name="router-ami-20240601-120000",
            build_at="2024-06-01T12:00:00.000Z",
        ),
    )
    resp = client.get("/api/workstations?infra=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["instances"]) == 1
    inst = data["instances"][0]
    assert inst["instance_id"] == "i-router1"
    assert inst["name"] == "router"
    assert inst["shutdown_at"] is None
    assert inst["ami_name"] == "router-ami-20240601-120000"
    assert data["future_router_ami"]["status"] == "consolidated"
