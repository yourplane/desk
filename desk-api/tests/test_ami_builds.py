"""Tests for AMI build API routes."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_LIST = {
    "items": [
        {
            "build_id": "b1",
            "ami_name": "my-ami",
            "created_at": "2026-01-01T00:00:00+00:00",
            "status_summary": {"phase": "build", "label": "Build step 1/2 in progress"},
        }
    ],
    "page": 1,
    "page_size": 20,
    "total": 1,
    "total_pages": 1,
}

MOCK_DETAIL = {
    "build_id": "b1",
    "ami_name": "my-ami",
    "status_summary": {"phase": "build", "label": "In progress"},
    "pipeline_complete": False,
}


@patch("app.routes.ami_builds.list_ami_builds", return_value=MOCK_LIST)
def test_list_ami_builds(mock_list: object) -> None:
    res = client.get("/api/ami-builds?page=1&page_size=20")
    assert res.status_code == 200
    data = res.json()
    assert data["items"][0]["build_id"] == "b1"
    assert data["total"] == 1
    mock_list.assert_called_once()


@patch("app.routes.ami_builds.list_ami_builds", return_value=MOCK_LIST)
def test_list_ami_builds_archived(mock_list: object) -> None:
    res = client.get("/api/ami-builds?archived=true")
    assert res.status_code == 200
    mock_list.assert_called_once()
    assert mock_list.call_args.kwargs["archived"] is True


@patch("app.routes.ami_builds.resolve_ami_build_snapshot")
@patch("app.routes.ami_builds.status_detail", return_value=MOCK_DETAIL)
def test_get_ami_build_detail(mock_detail: object, mock_resolve: object) -> None:
    mock_resolve.return_value = object()
    res = client.get("/api/ami-builds/b1?verbose=true")
    assert res.status_code == 200
    assert res.json()["build_id"] == "b1"
    mock_detail.assert_called_once()
    assert mock_detail.call_args.kwargs["verbose"] is True


@patch("app.routes.ami_builds.archive_ami_build")
def test_cancel_ami_build(mock_archive: object) -> None:
    res = client.post("/api/ami-builds/b1/cancel")
    assert res.status_code == 200
    assert res.json() == {"build_id": "b1", "archived": True}
    mock_archive.assert_called_once_with("b1", region=mock_archive.call_args.kwargs["region"], profile=mock_archive.call_args.kwargs["profile"])
