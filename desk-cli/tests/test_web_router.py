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
    assert "probe" in result.output
    assert "sync" in result.output


def test_desk_web_router_sync_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "sync", "--help"])
    assert result.exit_code == 0
    assert "Caddyfile" in result.output or "reload" in result.output.lower()


def test_desk_web_router_probe_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "probe", "--help"])
    assert result.exit_code == 0
    assert "health" in result.output.lower()


@patch("desk_cli.commands.web_router._http_probe_get")
@patch("desk_cli.commands.route._pid_alive", return_value=True)
def test_desk_web_router_probe_suggests_sync_on_caddy_placeholder_404(
    _mock_pid: object,
    mock_get: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    mock_get.side_effect = [
        (200, 2, "ok", None),
        (404, 69, "No matching desk route. Use desk route add and desk web-router start.", None),
    ]
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps(
            [{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 1}],
        )
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "probe"])
    assert result.exit_code == 0
    assert "desk web-router sync" in result.output


@patch("desk_cli.commands.web_router._caddyfile_out_of_sync_with_active_routes", return_value=False)
@patch("desk_cli.commands.web_router._http_probe_get")
@patch("desk_cli.commands.route._pid_alive", return_value=True)
def test_desk_web_router_probe_checks_active_route(
    _mock_pid: object,
    mock_get: MagicMock,
    _mock_sync: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    mock_get.side_effect = [
        (200, 2, "ok", None),
        (200, 12, "<!doctype ", None),
    ]
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps(
            [{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 1}],
        )
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "probe"])
    assert result.exit_code == 0
    assert "GET /health" in result.output
    assert "http://dev-80.localhost:8780/" in result.output  # default DESK_WEB_ROUTER_BASE_DOMAIN
    assert mock_get.call_count == 2


@patch("desk_cli.commands.web_router._run_caddy_reload")
@patch("desk_cli.commands.web_router._is_web_router_running", return_value=True)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
def test_desk_web_router_sync_writes_and_reloads(
    _mock_which: object,
    _mock_running: object,
    mock_reload: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps(
            [{"workstation": "dev", "remote_port": 8080, "local_port": 45000, "pid": 1}],
        )
    )
    runner = CliRunner()
    with patch("desk_cli.commands.route._pid_alive", return_value=True):
        result = runner.invoke(cli, ["web-router", "sync"])
    assert result.exit_code == 0
    assert "Wrote Caddyfile" in result.output
    assert "Reloaded Caddy" in result.output
    cf = tmp_path / "web-router" / "Caddyfile"
    assert cf.is_file()
    assert r"header_regexp Host ^dev\-8080\." in cf.read_text()
    mock_reload.assert_called_once()


@patch("desk_cli.commands.web_router._http_probe_get", return_value=(200, 2, "ok", None))
@patch("desk_cli.commands.route._pid_alive", return_value=False)
def test_desk_web_router_probe_warns_when_only_stale_routes(
    _mock_pid: object,
    _mock_get: MagicMock,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps(
            [{"workstation": "dev", "remote_port": 80, "local_port": 45001, "pid": 99999}],
        )
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["web-router", "probe"])
    assert result.exit_code == 0
    assert "stale" in result.output.lower()
    assert "No active" in result.output


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
    assert "dev-5001.localhost" in result.output
    caddyfile = tmp_path / "web-router" / "Caddyfile"
    assert caddyfile.is_file()
    text = caddyfile.read_text()
    assert "8780" in text
    assert "http://:8780" in text
    assert "bind 127.0.0.1" not in text
    assert "auto_https off" in text
    assert "admin 127.0.0.1:29789" in text
    assert r"header_regexp Host ^dev\-5001\." in text
    assert "@desk_route_" in text
    assert "reverse_proxy 127.0.0.1:45001" in text
    assert "header_up Host {http.request.host}" in text
    assert "versions 1.1" in text
    pid_file = tmp_path / "web-router" / "caddy.pid"
    assert pid_file.read_text().strip() == "4242"


@patch("desk_cli.commands.web_router._start_caddy_background", return_value=4242)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
def test_desk_web_router_start_multi_route_two_hosts(
    _mock_sysd: object,
    _mock_which: object,
    _mock_bg: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Multiple routes: separate host matchers per workstation/port (no path strip or cookies)."""
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
                },
                {
                    "workstation": "dev",
                    "remote_port": 5002,
                    "local_port": 45002,
                    "pid": 112,
                },
            ]
        )
    )

    runner = CliRunner()
    with patch("desk_cli.commands.route._pid_alive", return_value=True):
        result = runner.invoke(cli, ["web-router", "start"])

    assert result.exit_code == 0
    text = (tmp_path / "web-router" / "Caddyfile").read_text()
    assert r"header_regexp Host ^dev\-5001\." in text
    assert r"header_regexp Host ^dev\-5002\." in text
    assert "reverse_proxy 127.0.0.1:45001" in text
    assert "reverse_proxy 127.0.0.1:45002" in text


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
    assert r"header_regexp Host ^dev\-80\." in caddyfile.read_text()
    _mock_reload.assert_not_called()


@patch("desk_cli.commands.web_router._start_caddy_background", return_value=4242)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
def test_desk_web_router_caddyfile_includes_session_keeper_on_public_domain(
    _mock_sysd: object,
    _mock_which: object,
    _mock_bg: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public base domain enables handle_response injection of apex session-keeper.js."""
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("DESK_WEB_ROUTER_BASE_DOMAIN", "desk.example.com")
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps([{"workstation": "dev", "remote_port": 5173, "local_port": 45001, "pid": 1}])
    )
    runner = CliRunner()
    with patch("desk_cli.commands.route._pid_alive", return_value=True):
        result = runner.invoke(cli, ["web-router", "start"])
    assert result.exit_code == 0
    text = (tmp_path / "web-router" / "Caddyfile").read_text()
    assert "handle_response" in text
    assert "https://desk.example.com/session-keeper.js" in text
    assert "*text/html*" in text


@patch("desk_cli.commands.web_router._start_caddy_background", return_value=4242)
@patch("desk_cli.commands.web_router._which_caddy", return_value="/bin/caddy")
@patch("desk_cli.commands.web_router._systemd_active", return_value=False)
def test_desk_web_router_caddyfile_skips_session_keeper_on_localhost(
    _mock_sysd: object,
    _mock_which: object,
    _mock_bg: object,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DESK_STATE_HOME", str(tmp_path))
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "routes.json").write_text(
        json.dumps([{"workstation": "dev", "remote_port": 5173, "local_port": 45001, "pid": 1}])
    )
    runner = CliRunner()
    with patch("desk_cli.commands.route._pid_alive", return_value=True):
        result = runner.invoke(cli, ["web-router", "start"])
    assert result.exit_code == 0
    text = (tmp_path / "web-router" / "Caddyfile").read_text()
    assert "session-keeper.js" not in text
    assert "handle_response" not in text
