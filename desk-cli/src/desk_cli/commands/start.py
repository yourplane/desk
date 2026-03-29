"""desk start - start a stopped workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, start_workstation
from desk.config import get_default_profile, get_default_region


@click.command("start")
@click.argument("workstation", required=True)
@click.option(
    "--shutdown",
    "shutdown_after",
    type=str,
    default="4h",
    show_default=True,
    help="Duration until auto-stop, e.g. 4h, 30m, 2h30m (0 to disable).",
)
def start(
    workstation: str,
    shutdown_after: str,
) -> None:
    """Start a stopped workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.

    AWS region and credential profile come from the environment or desk config.
    """
    region = get_default_region()
    profile = get_default_profile()

    try:
        instance_id = resolve_workstation(
            workstation,
            region=region,
            profile=profile,
            states=["stopped"],
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    click.echo(f"Starting {instance_id}...")
    start_workstation(
        instance_id,
        shutdown_after=shutdown_after,
        region=region,
        profile=profile,
    )
    click.secho("Started.", fg="green")
