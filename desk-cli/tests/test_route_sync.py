"""Tests for desk route-sync commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from desk_cli.cli import cli


def _read_routes(state_home: Path) -> list[dict]:
    path = state_home / "routes" / "routes.json"
    return json.loads(path.read_text())


def test_desk_route_sync_help() -> None:
    """desk route-sync --help succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "--help"])
    assert result.exit_code == 0
    assert "Sync local desk routes" in result.output
    assert "pull" in result.output


@patch("desk_cli.commands.route_sync.list_all_web_routes", return_value={})
@patch("desk_cli.commands.route_sync._terminate_route_pid", return_value=True)
@patch("desk_cli.commands.route_sync._pid_alive", return_value=True)
@patch("desk_cli.commands.route_sync._notify_web_router_after_route_change")
def test_route_sync_pull_removes_routes_not_in_s3(
    _mock_notify: object,
    _mock_alive: object,
    _mock_term: object,
    _mock_list_s3: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull removes local routes when S3 registry is empty."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_DATA_BUCKET", "bucket")
    route_dir = tmp_path / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "main",
                    "remote_port": 8080,
                    "local_port": 45001,
                    "pid": 12345,
                }
            ]
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "pull"])
    assert result.exit_code == 0
    assert "Removed 1 local route" in result.output
    assert _read_routes(tmp_path) == []
    _mock_notify.assert_called_once()


@patch("desk_cli.commands.route_sync.list_all_web_routes", return_value={"main": [8080]})
@patch("desk_cli.commands.route_sync._start_forward_process", return_value=(12345, "/tmp/route.log"))
@patch("desk_cli.commands.route_sync._pick_local_port", return_value=45001)
@patch("desk_cli.commands.route_sync.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.route_sync.resolve_workstation", return_value="i-abc123")
@patch("desk_cli.commands.route_sync._notify_web_router_after_route_change")
def test_route_sync_pull_adds_missing_routes(
    _mock_notify: object,
    _mock_resolve: object,
    _mock_ssm: object,
    _mock_pick: object,
    _mock_start: object,
    _mock_list_s3: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull adds SSM routes for workstation/port pairs in S3."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_DATA_BUCKET", "bucket")

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "pull"])
    assert result.exit_code == 0
    assert "Added route main:8080 -> 127.0.0.1:45001" in result.output
    routes = _read_routes(tmp_path)
    assert len(routes) == 1
    assert routes[0]["workstation"] == "main"
    assert routes[0]["remote_port"] == 8080
    _mock_notify.assert_called_once()


@patch("desk_cli.commands.route_sync.list_all_web_routes", return_value={"main": [8080]})
@patch("desk_cli.commands.route_sync._notify_web_router_after_route_change")
def test_route_sync_pull_noop_when_already_synced(
    _mock_notify: object,
    _mock_list_s3: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull prints match message when local state already matches S3."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_DATA_BUCKET", "bucket")
    route_dir = tmp_path / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "main",
                    "instance_id": "i-abc",
                    "remote_port": 8080,
                    "local_port": 45001,
                    "pid": 1,
                }
            ]
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "pull"])
    assert result.exit_code == 0
    assert "Local routes already match S3." in result.output
    _mock_notify.assert_not_called()


@patch("desk_cli.commands.route_sync.get_desk_data_bucket", return_value="resolved-bucket")
@patch("desk_cli.commands.route_sync.list_all_web_routes", return_value={})
def test_route_sync_pull_resolves_bucket_when_env_unset(
    _mock_list: object,
    _mock_bucket: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull sets DESK_DATA_BUCKET from CloudFormation when unset."""
    monkeypatch.delenv("DESK_DATA_BUCKET", raising=False)
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "pull"])
    assert result.exit_code == 0
    _mock_bucket.assert_called_once()
    assert os.environ.get("DESK_DATA_BUCKET") == "resolved-bucket"
