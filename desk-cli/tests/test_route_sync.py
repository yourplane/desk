"""Tests for desk route-sync commands."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    assert "start" in result.output
    assert "stop" in result.output
    assert "status" in result.output


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


@patch("desk_cli.commands.route._pid_alive", return_value=True)
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
    _mock_pid_route: object,
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
@patch("desk_cli.commands.route._start_forward_process", return_value=(4242, "/tmp/refreshed.log"))
@patch("desk_cli.commands.route._pick_local_port", return_value=45002)
@patch("desk_cli.commands.route.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.route.resolve_workstation", return_value="i-abc123")
@patch("desk_cli.commands.route._pid_alive", side_effect=lambda pid: pid != 99999)
@patch("desk_cli.commands.route._notify_web_router_after_route_change")
def test_route_sync_pull_refreshes_stale_routes_still_in_s3(
    _mock_notify: object,
    _mock_alive: object,
    _mock_resolve_route: object,
    _mock_ssm_route: object,
    _mock_pick_route: object,
    _mock_start_route: object,
    _mock_list_s3: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pull restarts SSM forwards when local state is stale but S3 still wants the route."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_DATA_BUCKET", "bucket")
    route_dir = tmp_path / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "main",
                    "instance_id": "i-abc123",
                    "remote_port": 8080,
                    "local_port": 45001,
                    "pid": 99999,
                }
            ]
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "pull"])
    assert result.exit_code == 0
    assert "Refreshed route main:8080" in result.output
    routes = _read_routes(tmp_path)
    assert len(routes) == 1
    assert routes[0]["pid"] == 4242
    assert routes[0]["local_port"] == 45002
    assert _mock_notify.call_count >= 1


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


@patch("desk_cli.commands.route_sync.sys.platform", "linux")
@patch("desk_cli.commands.route_sync._systemctl_user")
@patch("desk_cli.commands.route_sync._install_route_sync_systemd_units")
def test_route_sync_start_on_boot_enables_timer(
    mock_install: MagicMock,
    mock_systemctl: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """desk route-sync start --on-boot installs units and runs enable --now."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))

    def _fake_ctl(args: list[str]) -> object:
        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    mock_systemctl.side_effect = _fake_ctl

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "start", "--on-boot", "--interval", "10"])
    assert result.exit_code == 0
    mock_install.assert_called_once_with(interval_seconds=10)
    assert mock_systemctl.call_args_list[-1][0][0] == ["enable", "--now", "desk-route-sync-pull.timer"]
    assert "persists on boot" in result.output


@patch("desk_cli.commands.route_sync.sys.platform", "linux")
@patch("desk_cli.commands.route_sync._systemctl_user")
@patch("desk_cli.commands.route_sync._install_route_sync_systemd_units")
def test_route_sync_start_without_on_boot_only_starts_timer(
    mock_install: MagicMock,
    mock_systemctl: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))

    def _fake_ctl(args: list[str]) -> object:
        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    mock_systemctl.side_effect = _fake_ctl

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "start", "--interval", "15"])
    assert result.exit_code == 0
    mock_install.assert_called_once_with(interval_seconds=15)
    assert mock_systemctl.call_args_list[-1][0][0] == ["start", "desk-route-sync-pull.timer"]
    assert "current session" in result.output


@patch("desk_cli.commands.route_sync.sys.platform", "linux")
@patch("desk_cli.commands.route_sync._disable_route_sync_systemd_units")
def test_route_sync_stop_on_boot_removes_units(mock_disable: MagicMock) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "stop", "--on-boot"])
    assert result.exit_code == 0
    mock_disable.assert_called_once_with(remove_unit_files=True)


@patch("desk_cli.commands.route_sync.sys.platform", "linux")
@patch("desk_cli.commands.route_sync._stop_route_sync_timer")
def test_route_sync_stop_stops_timer(mock_stop: MagicMock) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "stop"])
    assert result.exit_code == 0
    mock_stop.assert_called_once()


@patch("desk_cli.commands.route_sync._timer_enabled", return_value=True)
@patch("desk_cli.commands.route_sync._timer_active", return_value=True)
def test_route_sync_status_shows_enabled(
    _mock_act: object,
    _mock_en: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = tmp_path / ".config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    ud = cfg / "systemd" / "user"
    ud.mkdir(parents=True)
    (ud / "desk-route-sync-pull.timer").write_text("[Timer]\nOnUnitActiveSec=10s\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["route-sync", "status"])
    assert result.exit_code == 0
    assert "desk-route-sync-pull.timer" in result.output
    assert "10s" in result.output


@patch("desk_cli.commands.route_sync._notify_web_router_after_route_change")
@patch("desk_cli.commands.route._pid_alive", side_effect=lambda pid: pid != 99999)
@patch("desk_cli.commands.route._start_forward_process", return_value=(4242, "/tmp/refreshed.log"))
@patch("desk_cli.commands.route._pick_local_port", return_value=45002)
@patch("desk_cli.commands.route.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.route.resolve_workstation", return_value="i-dev")
@patch("desk_cli.commands.route_sync.list_all_web_routes", return_value={"dev": [8080], "foo": [5173]})
def test_route_sync_pull_skips_unknown_workstation_and_refreshes_stale(
    _mock_list_s3: object,
    _mock_resolve_route: object,
    _mock_ssm_route: object,
    _mock_pick_route: object,
    _mock_start_route: object,
    _mock_alive: object,
    _mock_notify: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown S3 workstations must not block refreshing stale routes for real workstations."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_DATA_BUCKET", "bucket")
    route_dir = tmp_path / "routes"
    route_dir.mkdir(parents=True, exist_ok=True)
    (route_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "dev",
                    "remote_port": 8080,
                    "local_port": 45001,
                    "pid": 99999,
                }
            ]
        )
    )

    def resolve_side_effect(ws: str, **_: object) -> str:
        if ws == "foo":
            raise ValueError("Workstation 'foo' not found. Run 'desk list' to see workstations.")
        return "i-dev"

    with patch("desk_cli.commands.route_sync.resolve_workstation", side_effect=resolve_side_effect):
        runner = CliRunner()
        result = runner.invoke(cli, ["route-sync", "pull"])

    assert result.exit_code == 0
    assert "Skipping foo:5173" in result.output
    assert "Refreshed route dev:8080" in result.output
    routes = _read_routes(tmp_path)
    assert len(routes) == 1
    assert routes[0]["pid"] == 4242
