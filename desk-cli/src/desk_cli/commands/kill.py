"""desk kill - terminate a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_router, resolve_workstation, terminate_instance
from desk.config import get_desk_settings


@click.command("kill")
@click.argument("workstation", required=True)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--infra",
    is_flag=True,
    default=False,
    help="Target the managed router (Type=router).",
)
def kill(
    workstation: str,
    yes: bool,
    infra: bool,
) -> None:
    """Terminate a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Use --infra to terminate the managed router (the ASG will typically launch a replacement).

    This permanently destroys the instance and all data on its root volume.

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        if infra:
            instance_id = resolve_router(
                workstation,
                region=region,
                profile=profile,
                states=["pending", "running", "stopping", "stopped"],
            )
        else:
            instance_id = resolve_workstation(
                workstation,
                region=region,
                profile=profile,
                states=["pending", "running", "stopping", "stopped"],
            )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if not yes:
        click.confirm(
            f"Terminate {instance_id}? This cannot be undone.",
            abort=True,
        )

    click.echo(f"Terminating {instance_id}...")
    terminate_instance(instance_id, region=region, profile=profile)
    click.secho("Terminated.", fg="red")
