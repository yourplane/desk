"""desk stop - stop a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, stop_instance
from desk.config import get_desk_settings


@click.command("stop")
@click.argument("workstation", required=True)
@click.option(
    "--infra",
    is_flag=True,
    default=False,
    help="Target the managed router (Type=router).",
)
def stop(workstation: str, infra: bool) -> None:
    """Stop a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Use --infra to stop the managed router (see ``desk list --infra``).

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = resolve_workstation(
            workstation, region=region, profile=profile, infra=infra
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    click.echo(f"Stopping {instance_id}...")
    stop_instance(instance_id, region=region, profile=profile)
    click.secho("Stopped.", fg="green")
