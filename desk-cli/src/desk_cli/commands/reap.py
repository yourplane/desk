"""desk reap - stop all workstations past their auto-stop time."""

from __future__ import annotations

import os

import click

from desk.aws import reap_overdue
from desk.config import get_default_profile, get_default_region


@click.command("reap")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be stopped without actually stopping.",
)
def reap(dry_run: bool) -> None:
    """Stop all workstations past their auto-stop time.

    Finds running instances with a desk:shutdown-at tag in the past
    and stops them. Use --dry-run to preview without stopping.

    AWS region and credential profile come from the environment or desk config.
    """
    region = get_default_region()
    profile = get_default_profile()

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
