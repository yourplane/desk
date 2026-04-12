"""desk route-sync — reconcile local SSM routes with the S3 web-routes registry."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

import click

from desk.aws import (
    RESERVED_INFRA_WORKSTATION_NAME,
    get_desk_data_bucket,
    is_ssm_ready,
    resolve_router,
    resolve_workstation,
    wait_for_ssm_ready,
)
from desk.config import get_desk_settings
from desk.web_routes import list_all_web_routes

from desk_cli.commands.route import (
    _load_routes,
    _notify_web_router_after_route_change,
    _parse_port_range,
    _pick_local_port,
    _pid_alive,
    _refresh_stale_routes,
    _save_routes,
    _start_forward_process,
    _terminate_route_pid,
)

SERVICE_UNIT = "desk-route-sync-pull.service"
TIMER_UNIT = "desk-route-sync-pull.timer"


def _route_key(route: dict[str, Any]) -> tuple[str, int]:
    ws = str(route.get("workstation", "")).strip()
    rp = int(route.get("remote_port", 0) or 0)
    return (ws, rp)


def _build_desired_set(s3_map: dict[str, list[int]]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for ws, ports in s3_map.items():
        name = str(ws).strip()
        if not name:
            continue
        for p in ports:
            try:
                out.add((name, int(p)))
            except (TypeError, ValueError):
                continue
    return out


def run_route_sync_pull(
    *,
    wait: bool = True,
    wait_timeout: int = 300,
    local_port_start: int | None = None,
    local_port_end: int | None = None,
    stack_name: str | None = None,
) -> None:
    """Pull web routes from S3 and sync local SSM port forwards. Raises ClickException on failure."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    if not os.environ.get("DESK_DATA_BUCKET"):
        try:
            os.environ["DESK_DATA_BUCKET"] = get_desk_data_bucket(
                stack_name=stack_name,
                region=region,
                profile=profile,
            )
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    try:
        s3_map = list_all_web_routes()
    except Exception as exc:
        raise click.ClickException(f"Failed to load web routes from S3: {exc}") from exc

    desired = _build_desired_set(s3_map)
    routes = _load_routes()

    to_keep: list[dict[str, Any]] = []
    removed = 0
    for r in routes:
        key = _route_key(r)
        if key in desired:
            to_keep.append(r)
            continue
        removed += 1
        pid = int(r.get("pid", 0) or 0)
        if _pid_alive(pid):
            _terminate_route_pid(pid)

    if removed:
        _save_routes(to_keep)
        _notify_web_router_after_route_change()
        click.echo(f"Removed {removed} local route(s) not present in S3.")

    current_keys = {_route_key(r) for r in _load_routes()}
    missing = sorted(desired - current_keys)
    start, end = _parse_port_range(local_port_start, local_port_end)

    added = 0
    for ws, port in missing:
        routes_now = _load_routes()
        dup = next(
            (x for x in routes_now if x.get("workstation") == ws and x.get("remote_port") == port),
            None,
        )
        if dup:
            continue

        try:
            if ws == RESERVED_INFRA_WORKSTATION_NAME:
                instance_id = resolve_router(ws, region=region, profile=profile)
            else:
                instance_id = resolve_workstation(ws, region=region, profile=profile)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc

        if not is_ssm_ready(instance_id, region=region, profile=profile):
            if not wait:
                raise click.ClickException(
                    f"Instance {instance_id} ({ws}) is not SSM-ready. Retry with --wait or when online."
                )
            if not wait_for_ssm_ready(instance_id, region=region, profile=profile, timeout=wait_timeout):
                raise click.ClickException(
                    f"Instance {instance_id} ({ws}) did not become SSM-ready within {wait_timeout}s."
                )

        routes_now = _load_routes()
        local_port = _pick_local_port(routes_now, start, end)
        pid, log_path = _start_forward_process(
            instance_id=instance_id,
            workstation=ws,
            remote_port=port,
            local_port=local_port,
            region=region,
            profile=profile,
        )
        route = {
            "workstation": ws,
            "instance_id": instance_id,
            "remote_port": port,
            "local_port": local_port,
            "pid": pid,
            "created_at": int(time.time()),
            "bind_host": "127.0.0.1",
            "log_path": log_path,
        }
        if ws == RESERVED_INFRA_WORKSTATION_NAME:
            route["infra"] = True
        routes_now.append(route)
        _save_routes(routes_now)
        _notify_web_router_after_route_change()
        added += 1
        click.echo(f"Added route {ws}:{port} -> 127.0.0.1:{local_port} (pid {pid})")

    refreshed, refresh_failures = _refresh_stale_routes(
        wait=wait,
        wait_timeout=wait_timeout,
        local_port_start=local_port_start,
        local_port_end=local_port_end,
        region=region,
        profile=profile,
    )
    if refresh_failures:
        raise click.ClickException(f"Failed to refresh {len(refresh_failures)} stale route(s).")

    if removed == 0 and added == 0 and refreshed == 0:
        click.echo("Local routes already match S3.")


@click.group("route-sync")
def route_sync_group() -> None:
    """Sync local desk routes with the S3 web-routes registry."""
    pass


@route_sync_group.command("pull")
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for instances to be SSM-ready before adding routes.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM before failing (per added route).",
)
@click.option("--local-port-start", type=click.IntRange(1, 65535), default=None)
@click.option("--local-port-end", type=click.IntRange(1, 65535), default=None)
@click.option(
    "--stack-name",
    default=None,
    metavar="NAME",
    help=(
        "CloudFormation stack that exports DeskDataBucketName. "
        "If omitted, tries desk-web then desk (web app stack first, then VPC stack name)."
    ),
)
def route_sync_pull(
    wait: bool,
    wait_timeout: int,
    local_port_start: int | None,
    local_port_end: int | None,
    stack_name: str | None,
) -> None:
    """Pull web routes from S3 and sync local SSM port forwards to match."""
    run_route_sync_pull(
        wait=wait,
        wait_timeout=wait_timeout,
        local_port_start=local_port_start,
        local_port_end=local_port_end,
        stack_name=stack_name,
    )


def _systemd_user_dir() -> str:
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "systemd", "user")


def _service_unit_path() -> str:
    return os.path.join(_systemd_user_dir(), SERVICE_UNIT)


def _timer_unit_path() -> str:
    return os.path.join(_systemd_user_dir(), TIMER_UNIT)


def _systemd_env_block_for_pull() -> str:
    lines: list[str] = []
    if desk_state := os.environ.get("DESK_STATE_HOME"):
        lines.append(f"Environment=DESK_STATE_HOME={desk_state.replace('%', '%%')}")
    return "".join(f"{line}\n" for line in lines)


def _systemd_exec_start_pull() -> str:
    desk = shutil.which("desk")
    if desk:
        return f"{shlex.quote(desk)} route-sync pull"
    return f"{shlex.quote(sys.executable)} -m desk_cli route-sync pull"


def _systemctl_user(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _timer_active() -> bool:
    if sys.platform != "linux":
        return False
    r = _systemctl_user(["is-active", TIMER_UNIT])
    return r.returncode == 0 and r.stdout.strip() == "active"


def _timer_enabled() -> bool:
    if sys.platform != "linux":
        return False
    r = _systemctl_user(["is-enabled", TIMER_UNIT])
    return r.returncode == 0


def _install_route_sync_systemd_units(*, interval_seconds: int) -> None:
    if sys.platform != "linux":
        raise click.ClickException("route-sync systemd units require Linux with systemd (user session).")
    if interval_seconds < 1:
        raise click.ClickException("interval must be at least 1 second.")

    unit_dir = _systemd_user_dir()
    os.makedirs(unit_dir, exist_ok=True)

    env_block = _systemd_env_block_for_pull()
    exec_line = _systemd_exec_start_pull()
    service_body = (
        "[Unit]\n"
        "Description=Desk route-sync pull (S3 registry to local SSM routes)\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        # Default KillMode=control-group would SIGTERM aws ssm start-session children when pull exits.
        "KillMode=none\n"
        f"{env_block}"
        f"ExecStart={exec_line}\n"
    )
    timer_body = (
        "[Unit]\n"
        "Description=Periodic desk route-sync pull\n"
        "\n"
        "[Timer]\n"
        f"OnBootSec={interval_seconds}s\n"
        f"OnUnitActiveSec={interval_seconds}s\n"
        "AccuracySec=1s\n"
        f"Unit={SERVICE_UNIT}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )

    with open(_service_unit_path(), "w", encoding="utf-8") as f:
        f.write(service_body)
    with open(_timer_unit_path(), "w", encoding="utf-8") as f:
        f.write(timer_body)

    dr = _systemctl_user(["daemon-reload"])
    if dr.returncode != 0:
        raise click.ClickException(
            "systemctl --user daemon-reload failed: "
            + (dr.stderr.strip() or dr.stdout.strip() or f"exit {dr.returncode}")
        )


def _disable_route_sync_systemd_units(*, remove_unit_files: bool) -> None:
    if sys.platform != "linux":
        return
    _systemctl_user(["disable", "--now", TIMER_UNIT])
    if remove_unit_files:
        for path in (_timer_unit_path(), _service_unit_path()):
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        _systemctl_user(["daemon-reload"])


def _stop_route_sync_timer() -> None:
    if sys.platform != "linux":
        return
    _systemctl_user(["stop", TIMER_UNIT])


@route_sync_group.command("start")
@click.option(
    "--on-boot",
    is_flag=True,
    help="Install systemd user units and enable the timer to run on boot (use with loginctl enable-linger).",
)
@click.option(
    "--foreground",
    is_flag=True,
    help="Run `desk route-sync pull` in a loop in the foreground (interval seconds between runs).",
)
@click.option(
    "--interval",
    "interval_seconds",
    type=click.IntRange(1, 86400),
    default=10,
    show_default=True,
    help="Seconds between pull runs (systemd timer or foreground loop).",
)
def route_sync_start(on_boot: bool, foreground: bool, interval_seconds: int) -> None:
    """Start periodic route-sync (systemd user timer, or --foreground loop)."""
    if foreground and on_boot:
        raise click.UsageError("--foreground and --on-boot cannot be used together.")

    if foreground:
        click.echo(
            f"route-sync foreground: pulling every {interval_seconds}s (Ctrl+C to stop).",
            err=True,
        )
        while True:
            try:
                run_route_sync_pull()
            except click.ClickException as exc:
                click.echo(str(exc), err=True)
            except click.Abort:
                raise
            except Exception as exc:
                click.echo(f"Error: {exc}", err=True)
            time.sleep(float(interval_seconds))

    _install_route_sync_systemd_units(interval_seconds=interval_seconds)
    if on_boot:
        en = _systemctl_user(["enable", "--now", TIMER_UNIT])
        if en.returncode != 0:
            raise click.ClickException(
                "systemctl --user enable --now failed: "
                + (en.stderr.strip() or en.stdout.strip() or f"exit {en.returncode}")
            )
        click.echo("route-sync timer enabled and started (systemd user, persists on boot).")
    else:
        st = _systemctl_user(["start", TIMER_UNIT])
        if st.returncode != 0:
            raise click.ClickException(
                "systemctl --user start failed: "
                + (st.stderr.strip() or st.stdout.strip() or f"exit {st.returncode}")
            )
        click.echo("route-sync timer started (systemd user, current session).")
    click.echo(f"Unit: {TIMER_UNIT} / {SERVICE_UNIT}")
    click.echo(f"Interval: {interval_seconds}s")


@route_sync_group.command("stop")
@click.option(
    "--on-boot",
    is_flag=True,
    help="Disable the timer and remove systemd user unit files.",
)
def route_sync_stop(on_boot: bool) -> None:
    """Stop the route-sync timer (disable units and remove files with --on-boot)."""
    if on_boot:
        _disable_route_sync_systemd_units(remove_unit_files=True)
        click.echo("Disabled route-sync timer (systemd user units removed).")
        return

    _stop_route_sync_timer()
    click.echo("Stopped route-sync timer.")


@route_sync_group.command("status")
def route_sync_status() -> None:
    """Show route-sync timer state and unit file paths."""
    interval_hint = ""
    timer_path = _timer_unit_path()
    if os.path.isfile(timer_path):
        try:
            text = open(timer_path, encoding="utf-8").read()
            for line in text.splitlines():
                if line.strip().startswith("OnUnitActiveSec="):
                    interval_hint = line.strip().split("=", 1)[-1]
                    break
        except OSError:
            pass

    click.echo(f"Timer: {TIMER_UNIT}")
    if interval_hint:
        click.echo(f"Configured interval: {interval_hint}")
    en = _timer_enabled()
    act = _timer_active()
    if en:
        state = "active" if act else "inactive"
        click.echo(click.style(f"State: enabled ({state})", fg="green" if act else "yellow"))
    else:
        click.echo(click.style("State: not enabled", fg="yellow"))

    click.echo(f"Service unit: {_service_unit_path()}")
    click.echo(f"Timer unit: {timer_path}")

