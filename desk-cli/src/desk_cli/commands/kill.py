"""desk kill - terminate a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, terminate_instance
from desk.config import get_default_profile, get_default_region


@click.command("kill")
@click.argument("workstation", required=True)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def kill(
    workstation: str,
    yes: bool,
) -> None:
    """Terminate a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.

    This permanently destroys the instance and all data on its root volume.

    AWS region and credential profile come from the environment or desk config.
    """
    region = get_default_region()
    profile = get_default_profile()

    try:
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
