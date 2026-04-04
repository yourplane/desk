"""desk route-sync — reconcile local SSM routes with the S3 web-routes registry."""

from __future__ import annotations

import os
import time
from typing import Any

import click

from desk.aws import get_desk_data_bucket, is_ssm_ready, resolve_workstation, wait_for_ssm_ready
from desk.config import get_desk_settings
from desk.web_routes import list_all_web_routes

from desk_cli.commands.route import (
    _load_routes,
    _notify_web_router_after_route_change,
    _parse_port_range,
    _pick_local_port,
    _pid_alive,
    _save_routes,
    _start_forward_process,
    _terminate_route_pid,
)


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
        routes_now.append(route)
        _save_routes(routes_now)
        _notify_web_router_after_route_change()
        added += 1
        click.echo(f"Added route {ws}:{port} -> 127.0.0.1:{local_port} (pid {pid})")

    if removed == 0 and added == 0:
        click.echo("Local routes already match S3.")
