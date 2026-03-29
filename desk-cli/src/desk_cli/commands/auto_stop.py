"""desk auto-stop - set or change the auto-stop time on a workstation."""

from __future__ import annotations

import os

import click

from desk.aws import (
    clear_shutdown_tag,
    compute_shutdown_at,
    parse_duration,
    resolve_workstation,
    set_shutdown_tag,
)
from desk.config import get_desk_settings


@click.command("auto-stop")
@click.argument("workstation")
@click.argument("duration", default="4h")
@click.option(
    "--clear",
    is_flag=True,
    default=False,
    help="Remove the auto-stop timer instead of setting one.",
)
def auto_stop(
    workstation: str,
    duration: str,
    clear: bool,
) -> None:
    """Set or change the auto-stop time on a workstation.

    WORKSTATION is the name or instance ID.
    DURATION is how long from now until auto-stop (default: 4h).
    Accepts hours (4h), minutes (30m), or combined (2h30m).

    \b
    Examples:
      desk auto-stop main           # reset 'main' to 4h from now
      desk auto-stop main 8h        # set 'main' to 8h from now
      desk auto-stop main 30m       # set 'main' to 30 min from now
      desk auto-stop dev 2h30m      # set 'dev' to 2h30m from now
      desk auto-stop main --clear   # remove auto-stop timer
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if clear:
        clear_shutdown_tag(instance_id, region=region, profile=profile)
        click.secho(f"Auto-stop timer cleared for {workstation} ({instance_id}).", fg="green")
    else:
        try:
            hours = parse_duration(duration)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="'DURATION'") from e
        shutdown_time = compute_shutdown_at(hours)
        set_shutdown_tag(instance_id, shutdown_time, region=region, profile=profile)
        click.secho(
            f"Auto-stop set to {shutdown_time} ({duration} from now) "
            f"for {workstation} ({instance_id}).",
            fg="green",
        )
