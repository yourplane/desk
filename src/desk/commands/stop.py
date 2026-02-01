"""desk stop - stop a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import list_workstations, stop_instance


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


def _resolve_workstation(
    name_or_id: str,
    region: str | None,
    profile: str | None,
) -> str:
    """Resolve name or instance ID to instance ID. Raises if not found."""
    workstations = list_workstations(region=region, profile=profile)

    # Exact instance ID match
    if name_or_id.startswith("i-"):
        for w in workstations:
            if w.instance_id == name_or_id:
                return w.instance_id
        raise click.UsageError(f"Workstation '{name_or_id}' not found. Run 'desk list' to see workstations.")

    # Name match (case-sensitive)
    for w in workstations:
        if w.name == name_or_id:
            return w.instance_id

    raise click.UsageError(f"Workstation '{name_or_id}' not found. Run 'desk list' to see workstations.")


@click.command("stop")
@click.argument("workstation", required=True)
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
) -> None:
    """Stop a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    instance_id = _resolve_workstation(workstation, region, profile)

    click.echo(f"Stopping {instance_id}...")
    stop_instance(instance_id, region=region, profile=profile)
    click.secho("Stopped.", fg="green")
