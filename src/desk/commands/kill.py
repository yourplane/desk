"""desk kill - terminate a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, terminate_instance


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.command("kill")
@click.argument("workstation", required=True)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
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
def kill(
    workstation: str,
    yes: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Terminate a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.

    This permanently destroys the instance and all data on its root volume.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

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
