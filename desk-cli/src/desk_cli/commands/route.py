"""desk route - manage persistent local port forwards via SSM."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from typing import Any

import click

from desk.aws import is_ssm_ready, resolve_workstation, wait_for_ssm_ready
from desk.config import get_desk_settings, get_state_home

DEFAULT_LOCAL_PORT_START = 45000
DEFAULT_LOCAL_PORT_END = 45100


def _route_dir() -> str:
    return os.path.join(get_state_home(), "routes")


def _state_file() -> str:
    return os.path.join(_route_dir(), "routes.json")


def _logs_dir() -> str:
    return os.path.join(_route_dir(), "logs")


def _ensure_state_dirs() -> None:
    os.makedirs(_route_dir(), exist_ok=True)
    os.makedirs(_logs_dir(), exist_ok=True)


def _load_routes() -> list[dict[str, Any]]:
    path = _state_file()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
    except Exception:
        pass
    return []


def _save_routes(routes: list[dict[str, Any]]) -> None:
    _ensure_state_dirs()
    path = _state_file()
    fd, tmp_path = tempfile.mkstemp(prefix="routes-", suffix=".json", dir=_route_dir())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(routes, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _route_status(route: dict[str, Any]) -> str:
    pid = int(route.get("pid", 0) or 0)
    return "active" if _pid_alive(pid) else "stale"


def _port_is_available(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _pick_local_port(routes: list[dict[str, Any]], start: int, end: int) -> int:
    if start > end:
        raise click.ClickException(f"Invalid local port range: {start}-{end}")
    used = {
        int(route.get("local_port", 0) or 0)
        for route in routes
        if _route_status(route) == "active"
    }
    for port in range(start, end + 1):
        if port in used:
            continue
        if _port_is_available(port):
            return port
    raise click.ClickException(
        f"No available local ports in range {start}-{end}. "
        "Remove unused routes with 'desk route remove <workstation> <port>'."
    )


def _parse_port_range(start: int | None, end: int | None) -> tuple[int, int]:
    env_start = os.environ.get("DESK_ROUTE_PORT_START")
    env_end = os.environ.get("DESK_ROUTE_PORT_END")
    start_value = start if start is not None else int(env_start) if env_start else DEFAULT_LOCAL_PORT_START
    end_value = end if end is not None else int(env_end) if env_end else DEFAULT_LOCAL_PORT_END
    if start_value < 1 or end_value > 65535:
        raise click.ClickException("Local port range must be between 1 and 65535.")
    return start_value, end_value


def _build_session_command(instance_id: str, remote_port: int, local_port: int) -> list[str]:
    return [
        "aws",
        "ssm",
        "start-session",
        "--target",
        instance_id,
        "--document-name",
        "AWS-StartPortForwardingSession",
        "--parameters",
        f"portNumber={remote_port},localPortNumber={local_port}",
    ]


def _start_forward_process(
    *,
    instance_id: str,
    workstation: str,
    remote_port: int,
    local_port: int,
    region: str | None,
    profile: str | None,
) -> tuple[int, str]:
    _ensure_state_dirs()
    ts = int(time.time())
    log_path = os.path.join(_logs_dir(), f"{workstation}-{remote_port}-{local_port}-{ts}.log")
    command = _build_session_command(instance_id, remote_port, local_port)
    env = os.environ.copy()
    if region:
        env["AWS_REGION"] = region
    if profile:
        env["AWS_PROFILE"] = profile
    with open(log_path, "a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    # Give quick feedback if session exits immediately (bad args/plugin issues).
    time.sleep(0.4)
    if proc.poll() is not None:
        raise click.ClickException(
            "Failed to start SSM port forwarding session. "
            f"See log for details: {log_path}"
        )
    return proc.pid, log_path


def _notify_web_router_after_route_change() -> None:
    from desk_cli.commands.web_router import refresh_web_router_after_route_change

    refresh_web_router_after_route_change()


def _terminate_route_pid(pid: int, timeout_seconds: float = 5.0) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(0.1)
    return not _pid_alive(pid)


def _resolve_instance_id(workstation: str, region: str | None, profile: str | None) -> str:
    """Resolve workstation name to EC2 instance id (raises ValueError if unknown)."""
    return resolve_workstation(workstation, region=region, profile=profile)


def _ensure_instance_ssm_ready(
    instance_id: str,
    *,
    wait: bool,
    wait_timeout: int,
    region: str | None,
    profile: str | None,
) -> None:
    """Require SSM agent ready, optionally waiting (same behavior as ``route add``)."""
    if not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait:
            raise click.ClickException(
                f"Instance {instance_id} is not SSM-ready. Retry with --wait or once it is online."
            )
        if not wait_for_ssm_ready(instance_id, region=region, profile=profile, timeout=wait_timeout):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )


def _new_route_record(
    *,
    workstation: str,
    remote_port: int,
    instance_id: str,
    routes_for_local_port: list[dict[str, Any]],
    port_range_start: int,
    port_range_end: int,
    region: str | None,
    profile: str | None,
) -> dict[str, Any]:
    """Pick a local port, start the SSM forward, return a route dict (same as ``route add``)."""
    local_port = _pick_local_port(routes_for_local_port, port_range_start, port_range_end)
    pid, log_path = _start_forward_process(
        instance_id=instance_id,
        workstation=workstation,
        remote_port=remote_port,
        local_port=local_port,
        region=region,
        profile=profile,
    )
    return {
        "workstation": workstation,
        "instance_id": instance_id,
        "remote_port": remote_port,
        "local_port": local_port,
        "pid": pid,
        "created_at": int(time.time()),
        "bind_host": "127.0.0.1",
        "log_path": log_path,
    }


@click.group("route")
def route_group() -> None:
    """Manage persistent SSM port forwarding routes."""
    pass


@route_group.command("add")
@click.argument("workstation")
@click.argument("port", type=click.IntRange(1, 65535))
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for instance to be SSM-ready if not already.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM before failing.",
)
@click.option("--local-port-start", type=click.IntRange(1, 65535), default=None, help="Local port range start.")
@click.option("--local-port-end", type=click.IntRange(1, 65535), default=None, help="Local port range end.")
def route_add(
    workstation: str,
    port: int,
    wait: bool,
    wait_timeout: int,
    local_port_start: int | None,
    local_port_end: int | None,
) -> None:
    """Add a route to forward WORKSTATION PORT to a local port."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    start, end = _parse_port_range(local_port_start, local_port_end)
    routes = _load_routes()
    duplicate = next((r for r in routes if r.get("workstation") == workstation and r.get("remote_port") == port), None)
    if duplicate:
        status = _route_status(duplicate)
        raise click.ClickException(
            f"Route already exists for {workstation}:{port} (status: {status}). "
            f"Use 'desk route remove {workstation} {port}' first."
        )

    try:
        instance_id = _resolve_instance_id(workstation, region, profile)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    _ensure_instance_ssm_ready(
        instance_id,
        wait=wait,
        wait_timeout=wait_timeout,
        region=region,
        profile=profile,
    )

    route = _new_route_record(
        workstation=workstation,
        remote_port=port,
        instance_id=instance_id,
        routes_for_local_port=routes,
        port_range_start=start,
        port_range_end=end,
        region=region,
        profile=profile,
    )
    routes.append(route)
    _save_routes(routes)
    _notify_web_router_after_route_change()
    click.echo(f"Added route {workstation}:{port} -> 127.0.0.1:{route['local_port']} (pid {route['pid']})")


@route_group.command("remove")
@click.argument("workstation")
@click.argument("port", type=click.IntRange(1, 65535))
def route_remove(workstation: str, port: int) -> None:
    """Remove a route for WORKSTATION PORT."""
    routes = _load_routes()
    target = next((r for r in routes if r.get("workstation") == workstation and r.get("remote_port") == port), None)
    if not target:
        click.echo(f"No route found for {workstation}:{port}.")
        return

    pid = int(target.get("pid", 0) or 0)
    was_active = _pid_alive(pid)
    if was_active:
        _terminate_route_pid(pid)

    updated = [r for r in routes if not (r.get("workstation") == workstation and r.get("remote_port") == port)]
    _save_routes(updated)
    _notify_web_router_after_route_change()
    if was_active:
        click.echo(f"Removed route {workstation}:{port}.")
    else:
        click.echo(f"Removed stale route {workstation}:{port}.")


@route_group.command("clear")
def route_clear() -> None:
    """Remove stale routes (dead port-forward processes) from local state."""
    routes = _load_routes()
    active = [r for r in routes if _route_status(r) == "active"]
    removed = len(routes) - len(active)
    if removed == 0:
        click.echo("No stale routes.")
        return
    _save_routes(active)
    _notify_web_router_after_route_change()
    click.echo(f"Removed {removed} stale route(s).")


def _refresh_stale_routes(
    *,
    wait: bool,
    wait_timeout: int,
    local_port_start: int | None,
    local_port_end: int | None,
    region: str | None,
    profile: str | None,
) -> tuple[int, list[tuple[str, int, str]]]:
    """Restart SSM forwards for routes whose process has exited. Returns (refreshed_count, failures)."""
    start, end = _parse_port_range(local_port_start, local_port_end)
    routes = _load_routes()
    if not any(_route_status(r) == "stale" for r in routes):
        return 0, []

    new_routes: list[dict[str, Any]] = []
    failures: list[tuple[str, int, str]] = []
    refreshed = 0

    for r in routes:
        if _route_status(r) == "active":
            new_routes.append(r)
            continue

        ws = str(r.get("workstation", ""))
        port = int(r.get("remote_port", 0) or 0)

        try:
            instance_id = _resolve_instance_id(ws, region, profile)
        except ValueError as exc:
            failures.append((ws, port, str(exc)))
            new_routes.append(r)
            continue

        try:
            _ensure_instance_ssm_ready(
                instance_id,
                wait=wait,
                wait_timeout=wait_timeout,
                region=region,
                profile=profile,
            )
        except click.ClickException as exc:
            failures.append((ws, port, str(exc)))
            new_routes.append(r)
            continue

        try:
            route = _new_route_record(
                workstation=ws,
                remote_port=port,
                instance_id=instance_id,
                routes_for_local_port=new_routes,
                port_range_start=start,
                port_range_end=end,
                region=region,
                profile=profile,
            )
        except click.ClickException as exc:
            failures.append((ws, port, str(exc)))
            new_routes.append(r)
            continue

        new_routes.append(route)
        refreshed += 1
        click.echo(f"Refreshed route {ws}:{port} -> 127.0.0.1:{route['local_port']} (pid {route['pid']})")

    if refreshed:
        _save_routes(new_routes)
        _notify_web_router_after_route_change()

    for ws, port, msg in failures:
        click.echo(click.style(f"Failed to refresh {ws}:{port}: {msg}", fg="red"), err=True)

    return refreshed, failures


@route_group.command("refresh")
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for instance to be SSM-ready if not already.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM before failing (per stale route).",
)
@click.option("--local-port-start", type=click.IntRange(1, 65535), default=None, help="Local port range start.")
@click.option("--local-port-end", type=click.IntRange(1, 65535), default=None, help="Local port range end.")
def route_refresh(
    wait: bool,
    wait_timeout: int,
    local_port_start: int | None,
    local_port_end: int | None,
) -> None:
    """Restart SSM port forwards for routes whose process has exited (stale)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    refreshed, failures = _refresh_stale_routes(
        wait=wait,
        wait_timeout=wait_timeout,
        local_port_start=local_port_start,
        local_port_end=local_port_end,
        region=region,
        profile=profile,
    )
    if refreshed == 0 and not failures:
        click.echo("No stale routes.")
        return

    if failures:
        raise click.ClickException(f"Failed to refresh {len(failures)} route(s).")


@route_group.command("list")
def route_list() -> None:
    """List configured routes and process status."""
    routes = _load_routes()
    if not routes:
        click.echo("No routes found.")
        return

    max_workstation = max(11, max(len(str(r.get("workstation", "-"))) for r in routes))
    max_remote = 11
    max_local = 10
    max_status = 6
    header = (
        f"{'WORKSTATION':<{max_workstation}}  "
        f"{'REMOTE PORT':<{max_remote}}  "
        f"{'LOCAL PORT':<{max_local}}  "
        f"{'STATUS':<{max_status}}  PID"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for route in routes:
        status = _route_status(route)
        status_display = click.style(status, fg="green" if status == "active" else "yellow")
        click.echo(
            f"{str(route.get('workstation', '-')):<{max_workstation}}  "
            f"{str(route.get('remote_port', '-')):<{max_remote}}  "
            f"{str(route.get('local_port', '-')):<{max_local}}  "
            f"{status_display:<{max_status}}  {route.get('pid', '-')}"
        )
