"""Tests for desk web-router commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from desk_cli.cli import cli
from desk_cli.commands.web_router import refresh_web_router_after_route_change


def test_desk_web_router_help() -> None:
    """desk web-router --help succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "--help"])
    assert result.exit_code == 0
    assert "Caddy" in result.output
    assert "start" in result.output
    assert "stop" in result.output
    assert "status" in result.output


@patch("desk_cli.commands.web_router._start_caddy_background", return_value=4242)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
def test_desk_web_router_start_writes_caddyfile_and_pid(
    _mock_sysd: object,
    _mock_which: object,
    _mock_bg: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """desk web-router start creates Caddyfile and pid when caddy exists."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "routes.json").write_text(
        json.dumps(
            [
                {
                    "workstation": "dev",
                    "remote_port": 5001,
                    "local_port": 45001,
                    "pid": 111,
                }
            ]
        )
    )

    runner = CliRunner()
    with patch("desk_cli.commands.route._pid_alive", return_value=True):
        result = runner.invoke(cli, ["web-router", "start"])

    assert result.exit_code == 0
    assert "4242" in result.output
    assert "/dev/5001" in result.output or "workstation" in result.output
    caddyfile = tmp_path / "web-router" / "Caddyfile"
    assert caddyfile.is_file()
    text = caddyfile.read_text()
    assert "8780" in text
    assert "admin 127.0.0.1:29789" in text
    assert "handle_path /dev/5001" in text
    assert "reverse_proxy 127.0.0.1:45001" in text
    pid_file = tmp_path / "web-router" / "caddy.pid"
    assert pid_file.read_text().strip() == "4242"


@patch("desk_cli.commands.web_router._which_caddy", return_value=None)
def test_desk_web_router_start_requires_caddy(_mock: object, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "start"])
    assert result.exit_code != 0
    assert "caddy" in result.output.lower()


@patch("desk_cli.commands.web_router._terminate_caddy_pid", return_value=True)
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
@patch("desk_cli.commands.web_router._pid_alive", return_value=True)
def test_desk_web_router_stop_terminates_pid(
    _mock_alive: object,
    _mock_sysd: object,
    _mock_term: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    wr = tmp_path / "web-router"
    wr.mkdir(parents=True)
    (wr / "caddy.pid").write_text("999\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "stop"])
    assert result.exit_code == 0
    assert "999" in result.output
    assert not (wr / "caddy.pid").exists()


@patch("desk_cli.commands.web_router._systemd_enabled", return_value=False)
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
@patch("desk_cli.commands.web_router._pid_alive", return_value=False)
def test_desk_web_router_status_not_running(
    _mock_alive: object,
    _mock_active: object,
    _mock_enabled: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "status"])
    assert result.exit_code == 0
    assert "not running" in result.output
    assert "routes/logs" in result.output


@patch("desk_cli.commands.web_router._systemd_enabled", return_value=True)
@patch("desk_cli.commands.web_router._systemd_active", return_value=True)
@patch("desk_cli.commands.web_router._pid_alive", return_value=False)
def test_desk_web_router_status_systemd_running(
    _mock_alive: object,
    _mock_active: object,
    _mock_enabled: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "status"])
    assert result.exit_code == 0
    assert "systemd" in result.output
    assert "running" in result.output


@patch("desk_cli.commands.web_router.sys.platform", "linux")
@patch("desk_cli.commands.web_router._start_systemd_service")
@patch("desk_cli.commands.web_router._install_systemd_user_unit")
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
@patch("desk_cli.commands.web_router._pid_alive", return_value=False)
def test_desk_web_router_start_on_boot_uses_systemd(
    _mock_palive: object,
    _mock_sactive: object,
    _mock_which: object,
    _mock_install: MagicMock,
    _mock_start_svc: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir(parents=True)
    (routes_dir / "routes.json").write_text("[]")

    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "start", "--on-boot"])
    assert result.exit_code == 0
    _mock_install.assert_called_once()
    _mock_start_svc.assert_called_once()
    assert "systemd" in result.output


@patch("desk_cli.commands.web_router.sys.platform", "linux")
@patch("desk_cli.commands.web_router._disable_systemd_user_unit")
def test_desk_web_router_stop_on_boot_disables_unit(
    _mock_disable: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "stop", "--on-boot"])
    assert result.exit_code == 0
    _mock_disable.assert_called_once_with(remove_unit_file=True)


@patch("desk_cli.commands.web_router._run_caddy_reload")
@patch("desk_cli.commands.web_router._systemd_active", return_value=True)
@patch("desk_cli.commands.web_router._pid_alive", return_value=True)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
def test_refresh_runs_caddy_reload_when_systemd_active(
    _mock_which: object,
    _mock_alive: object,
    _mock_sysd: object,
    _mock_reload: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps([{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 1}])
    )
    refresh_web_router_after_route_change()
    _mock_reload.assert_called_once()
    cfg = _mock_reload.call_args[0][0]
    assert str(cfg).endswith("Caddyfile")


@patch("desk_cli.commands.web_router._run_caddy_reload")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
def test_refresh_runs_caddy_reload_when_manual_pid_running(
    _mock_which: object,
    _mock_sysd: object,
    _mock_reload: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps([{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 1}])
    )
    (tmp_path / "web-router").mkdir()
    (tmp_path / "web-router" / "caddy.pid").write_text("42\n")
    with patch("desk_cli.commands.web_router._pid_alive", return_value=True):
        refresh_web_router_after_route_change()
    _mock_reload.assert_called_once()


@patch("desk_cli.commands.web_router._run_caddy_reload")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
def test_refresh_writes_caddyfile_without_reload_when_router_stopped(
    _mock_which: object,
    _mock_sysd: object,
    _mock_reload: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps([{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 1}])
    )
    with patch("desk_cli.commands.web_router._pid_alive", return_value=True):
        refresh_web_router_after_route_change()
    caddyfile = tmp_path / "web-router" / "Caddyfile"
    assert caddyfile.is_file()
    assert "handle_path /dev/80" in caddyfile.read_text()
    _mock_reload.assert_not_called()
