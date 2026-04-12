"""desk start - start a stopped workstation instance."""

from __future__ import annotations

import click

from desk.aws import resolve_workstation_target, start_workstation
from desk.config import get_desk_settings


@click.command("start")
@click.argument("workstation", required=True)
@click.option(
    "--infra",
    is_flag=True,
    help="Allow targeting desk infrastructure instances (for example NAT).",
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
    infra: bool,
    shutdown_after: str,
) -> None:
    """Start a stopped workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = resolve_workstation_target(
            workstation,
            infra=infra,
            region=region,
            profile=profile,
            states=["stopped"],
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    click.echo(f"Starting {instance_id}...")
    start_workstation(
        instance_id,
        shutdown_after=shutdown_after,
        region=region,
        profile=profile,
    )
    click.secho("Started.", fg="green")
