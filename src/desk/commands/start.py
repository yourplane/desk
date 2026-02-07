"""desk start - start a stopped workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import compute_shutdown_at, parse_duration, resolve_workstation, set_shutdown_tag, start_instance


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
    region: str | None,
    profile: str | None,
    shutdown_after: str,
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

    shutdown_hours = parse_duration(shutdown_after)
    if shutdown_hours > 0:
        shutdown_time = compute_shutdown_at(shutdown_hours)
        set_shutdown_tag(instance_id, shutdown_time, region=region, profile=profile)

    click.secho("Started.", fg="green")
