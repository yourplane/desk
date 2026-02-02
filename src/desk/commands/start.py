"""desk start - start a stopped workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import resolve_workstation, start_instance


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.command("start")
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
def start(
    workstation: str,
    region: str | None,
    profile: str | None,
) -> None:
    """Start a stopped workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

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
    start_instance(instance_id, region=region, profile=profile)
    click.secho("Started.", fg="green")
