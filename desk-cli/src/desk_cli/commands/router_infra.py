"""desk router-infra - wake/sleep router stack and show status."""

from __future__ import annotations

import json

import click

from desk.config import get_desk_settings
from desk.router_infra import (
    get_router_infra_status,
    sleep_router_infra,
    wake_router_infra,
)


@click.group("router-infra")
def router_infra_group() -> None:
    """Manage desk router infra stack (wake/sleep ALB + ASG)."""


@router_infra_group.command("status")
def status_cmd() -> None:
    """Show routing-infra phase and component health."""
    aws = get_desk_settings().aws_settings
    info = get_router_infra_status(region=aws.region, profile=aws.profile)
    click.echo(json.dumps(info.__dict__, indent=2, default=str))


@router_infra_group.command("wake")
def wake_cmd() -> None:
    """Launch desk-router-active and scale router ASG (fire-and-forget)."""
    aws = get_desk_settings().aws_settings
    try:
        result = wake_router_infra(region=aws.region, profile=aws.profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(result, indent=2))
    click.secho("Router infra wake initiated.", fg="green")


@router_infra_group.command("sleep")
@click.option(
    "--force",
    is_flag=True,
    help="Shut down even when pending/running workstations have web-route ports.",
)
def sleep_cmd(force: bool) -> None:
    """Shut down desk-router-active and scale router ASG to zero."""
    aws = get_desk_settings().aws_settings
    try:
        result = sleep_router_infra(region=aws.region, profile=aws.profile, force=force)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo(json.dumps(result, indent=2))
    click.secho("Router infra sleep initiated.", fg="green")
