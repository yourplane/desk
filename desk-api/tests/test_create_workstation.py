"""Tests for POST /api/workstations (create workstation)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@patch("app.routes.workstations.run_workstation")
@patch("app.routes.workstations.get_latest_ubuntu_ami")
@patch("app.routes.workstations.get_desk_vpc_outputs")
@patch("app.routes.workstations.get_default_ami_prefix")
@patch("app.routes.workstations.list_workstations")
def test_create_workstation_success(
    mock_list: object,
    mock_ami_prefix: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
) -> None:
    """POST /api/workstations creates a new workstation and returns its details."""
    mock_list.return_value = []
    mock_ami_prefix.return_value = None
    mock_vpc.return_value = type(
        "V",
        (),
        {
            "private_subnet_ids": ["subnet-1"],
            "security_group_id": "sg-1",
            "instance_profile_name": "profile-1",
        },
    )()
    mock_ami.return_value = "ami-123"
    mock_run.return_value = ("i-new123", "2026-03-20T20:00:00Z")

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance_id"] == "i-new123"
    assert body["name"] == "my-ws"
    assert body["shutdown_at"] == "2026-03-20T20:00:00Z"
    mock_run.assert_called_once()
    call_kw = mock_run.call_args[1]
    assert call_kw["name"] == "my-ws"
    assert call_kw["instance_type"] == "t3.medium"
    assert call_kw["shutdown_after"] == "4h"


@patch("app.routes.workstations.run_workstation")
@patch("app.routes.workstations.get_latest_ami_by_name_prefix")
@patch("app.routes.workstations.get_desk_vpc_outputs")
@patch("app.routes.workstations.get_default_ami_prefix")
@patch("app.routes.workstations.list_workstations")
def test_create_workstation_custom_instance_type(
    mock_list: object,
    mock_ami_prefix: object,
    mock_vpc: object,
    mock_ami_by_prefix: object,
    mock_run: object,
) -> None:
    """POST /api/workstations accepts a custom instance_type."""
    mock_list.return_value = []
    mock_ami_prefix.return_value = "default-desk-ami"
    mock_ami_by_prefix.return_value = "ami-custom"
    mock_vpc.return_value = type(
        "V",
        (),
        {
            "private_subnet_ids": ["subnet-1"],
            "security_group_id": "sg-1",
            "instance_profile_name": "profile-1",
        },
    )()
    mock_run.return_value = ("i-new456", "2026-03-20T20:00:00Z")

    resp = client.post(
        "/api/workstations",
        json={"name": "big-ws", "instance_type": "m5.xlarge"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance_id"] == "i-new456"
    assert body["name"] == "big-ws"
    call_kw = mock_run.call_args[1]
    assert call_kw["instance_type"] == "m5.xlarge"
    assert call_kw["ami_id"] == "ami-custom"


@patch("app.routes.workstations.list_workstations")
def test_create_workstation_duplicate_name_rejected(mock_list: object) -> None:
    """POST /api/workstations returns 409 when a non-terminated workstation with the same name exists."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-existing", name="my-ws", state="running"),
    ]

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]
    assert "i-existing" in resp.json()["detail"]


@patch("app.routes.workstations.list_workstations")
def test_create_workstation_duplicate_stopped_rejected(mock_list: object) -> None:
    """POST /api/workstations rejects duplicate even if the existing workstation is stopped."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="my-ws", state="stopped"),
    ]

    resp = client.post("/api/workstations", json={"name": "my-ws"})

    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


@patch("app.routes.workstations.run_workstation")
@patch("app.routes.workstations.get_latest_ubuntu_ami")
@patch("app.routes.workstations.get_desk_vpc_outputs")
@patch("app.routes.workstations.get_default_ami_prefix")
@patch("app.routes.workstations.list_workstations")
def test_create_workstation_allows_terminated_duplicate(
    mock_list: object,
    mock_ami_prefix: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
) -> None:
    """POST /api/workstations allows creating when the only existing workstation with that name is terminated."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-old", name="my-ws", state="terminated"),
    ]
    mock_ami_prefix.return_value = None
    mock_vpc.return_value = type(
        "V",
        (),
        {
            "private_subnet_ids": ["subnet-1"],
            "security_group_id": "sg-1",
            "instance_profile_name": "profile-1",
        },
    )()
    mock_ami.return_value = "ami-123"
    mock_run.return_value = ("i-new789", "2026-03-20T20:00:00Z")

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
