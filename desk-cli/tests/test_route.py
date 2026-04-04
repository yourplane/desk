"""Tests for desk route commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from desk_cli.cli import cli


def _read_routes(state_home: Path) -> list[dict]:
    path = state_home / "routes" / "routes.json"
    return json.loads(path.read_text())


def _read_routes_at_state_root(state_root: Path, desk_profile: str | None = None) -> list[dict]:
    base = state_root / desk_profile if desk_profile else state_root
    return _read_routes(base)


def test_desk_route_help() -> None:
    """desk route --help succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["route", "--help"])
    assert result.exit_code == 0
    assert "Manage persistent SSM port forwarding routes" in result.output
    assert "add" in result.output
    assert "remove" in result.output
    assert "list" in result.output


@patch("desk_cli.commands.web_router.refresh_web_router_after_route_change")
@patch("desk_cli.commands.route._start_forward_process", return_value=(12345, "/tmp/route.log"))
@patch("desk_cli.commands.route._pick_local_port", return_value=45001)
@patch("desk_cli.commands.route.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.route.resolve_workstation", return_value="i-abc123")
def test_desk_route_add_saves_route(
    _mock_resolve: object,
    _mock_ssm_ready: object,
    _mock_pick_port: object,
    _mock_start_forward: object,
    _mock_refresh: object,
    tmp_path,
    monkeypatch,
) -> None:
    """desk route add stores route metadata and prints local port."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["route", "add", "main", "8080"])

    assert result.exit_code == 0
    assert "Added route main:8080 -> 127.0.0.1:45001" in result.output
    routes = _read_routes(tmp_path)
    assert len(routes) == 1
    route = routes[0]
    assert route["workstation"] == "main"
    assert route["remote_port"] == 8080
    assert route["local_port"] == 45001
    assert route["pid"] == 12345
    assert route["bind_host"] == "127.0.0.1"
    _mock_refresh.assert_called_once()


@patch("desk_cli.commands.route._start_forward_process", return_value=(12345, "/tmp/route.log"))
@patch("desk_cli.commands.route._pick_local_port", return_value=45001)
@patch("desk_cli.commands.route.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.route.resolve_workstation", return_value="i-abc123")
def test_desk_route_add_namespaces_state_with_desk_profile(
    _mock_resolve: object,
    _mock_ssm_ready: object,
    _mock_pick_port: object,
    _mock_start_forward: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root --profile (desk profile) stores routes under DESK_STATE_HOME/<profile>/."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--profile", "work", "route", "add", "main", "8080"],
    )
    assert result.exit_code == 0
    routes = _read_routes_at_state_root(tmp_path, "work")
    assert len(routes) == 1


def test_desk_route_add_rejects_duplicate(tmp_path, monkeypatch) -> None:
    """desk route add fails for duplicate workstation/port."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    route_dir = tmp_path / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "main",
                    "remote_port": 8080,
                    "local_port": 45001,
                    "pid": 99999,
                }
            ]
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["route", "add", "main", "8080"])
    assert result.exit_code != 0
    assert "Route already exists" in result.output


@patch("desk_cli.commands.web_router.refresh_web_router_after_route_change")
@patch("desk_cli.commands.route._terminate_route_pid", return_value=True)
@patch("desk_cli.commands.route._pid_alive", return_value=True)
def test_desk_route_remove_removes_entry(
    _mock_pid_alive: object,
    _mock_terminate: object,
    _mock_refresh: object,
    tmp_path,
    monkeypatch,
) -> None:
    """desk route remove terminates process and deletes route state."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
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
    result = runner.invoke(cli, ["route", "remove", "main", "8080"])
    assert result.exit_code == 0
    assert "Removed route main:8080" in result.output
    assert _read_routes(tmp_path) == []
    _mock_refresh.assert_called_once()


@patch("desk_cli.commands.route._pid_alive", return_value=False)
def test_desk_route_list_shows_stale(_mock_pid_alive: object, tmp_path, monkeypatch) -> None:
    """desk route list reports stale routes when process is gone."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
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
    result = runner.invoke(cli, ["route", "list"])
    assert result.exit_code == 0
    assert "WORKSTATION" in result.output
    assert "main" in result.output
    assert "stale" in result.output
