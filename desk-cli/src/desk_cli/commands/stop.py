"""desk stop - stop a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation_target, stop_instance
from desk.config import get_default_profile, get_default_region


@click.command("stop")
@click.argument("workstation", required=True)
@click.option(
    "--infra",
    is_flag=True,
    help="Allow targeting desk infrastructure instances (for example NAT).",
)
@click.option(
    "--region",
    "-r",
    default=None,
    envvar="AWS_REGION",
    help="AWS region.",
)
@click.option(
    "--profile",
    "-p",
    default=None,
    envvar="AWS_PROFILE",
    help="AWS profile.",
)
def stop(
    workstation: str,
    region: str | None,
    profile: str | None,
    infra: bool,
) -> None:
    """Stop a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    try:
        instance_id = resolve_workstation_target(
            workstation,
            infra=infra,
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    click.echo(f"Stopping {instance_id}...")
    stop_instance(instance_id, region=region, profile=profile)
    click.secho("Stopped.", fg="green")
