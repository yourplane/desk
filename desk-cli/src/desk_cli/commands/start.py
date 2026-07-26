"""desk start - start a stopped workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, start_workstation
from desk.config import get_desk_settings
from desk.router_infra import ensure_router_up, is_router_instance_ops_enabled


@click.command("start")
@click.argument("workstation", required=True)
@click.option(
    "--shutdown",
    "shutdown_after",
    type=str,
    default="4h",
    show_default=True,
    help="Duration until auto-stop, e.g. 4h, 30m, 2h30m (0 to disable). Ignored with --infra.",
)
@click.option(
    "--infra",
    is_flag=True,
    default=False,
    help="Target the managed router (Type=router).",
)
def start(
    workstation: str,
    shutdown_after: str,
    infra: bool,
) -> None:
    """Start a stopped workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Use --infra to start the managed router (``--shutdown`` is ignored).

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = resolve_workstation(
            workstation,
            region=region,
            profile=profile,
            states=["stopped"],
            infra=infra,
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if infra and not is_router_instance_ops_enabled(region=region, profile=profile):
        raise click.UsageError(
            "Router instance operations are disabled while the active stack is absent."
        )

    click.echo(f"Starting {instance_id}...")
    start_workstation(
        instance_id,
        shutdown_after=shutdown_after,
        region=region,
        profile=profile,
        infra=infra,
    )
    click.secho("Started.", fg="green")
    if not infra:
        try:
            ensure_router_up(region=region, profile=profile)
        except Exception:
            pass
