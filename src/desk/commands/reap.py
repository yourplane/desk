"""desk reap - stop all workstations past their auto-stop time."""

from __future__ import annotations

import os

import click

from desk.aws import reap_overdue


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.command("reap")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be stopped without actually stopping.",
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
def reap(
    dry_run: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Stop all workstations past their auto-stop time.

    Finds running instances with a desk:shutdown-at tag in the past
    and stops them. Use --dry-run to preview without stopping.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    overdue = reap_overdue(region=region, profile=profile, dry_run=dry_run)

    if not overdue:
        click.echo("No overdue workstations.")
        return

    for w in overdue:
        label = f"{w.name} ({w.instance_id})" if w.name else w.instance_id
        if dry_run:
            click.echo(f"  Would stop {label}  (shutdown was {w.shutdown_at})")
        else:
            click.echo(f"  Stopping {label}...")

    count = len(overdue)
    if dry_run:
        click.secho(f"\n{count} workstation(s) would be stopped.", fg="yellow")
    else:
        click.secho(f"\n{count} workstation(s) stopped.", fg="green")
