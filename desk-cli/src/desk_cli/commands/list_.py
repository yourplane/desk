"""desk list - list workstation instances."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import click

from desk.aws import Workstation, list_workstations
from desk.config import get_desk_settings


def _color_state(state: str) -> str:
    """Return state string with color for readability."""
    colors = {
        "running": "green",
        "pending": "yellow",
        "stopped": "red",
        "stopping": "yellow",
        "terminated": "red",
    }
    color = colors.get(state, None)
    return click.style(state, fg=color) if color else state


def _format_shutdown(shutdown_at: str | None, state: str = "running") -> tuple[str, int]:
    """Format the shutdown_at timestamp for display.

    Returns (display_string, raw_length) where display_string may contain
    ANSI color codes and raw_length is the visible character count.
    """
    if not shutdown_at:
        return "-", 1
    # Don't show OVERDUE for instances that are already stopping/stopped
    if state in ("stopped", "stopping", "terminated", "shutting-down"):
        return "-", 1
    try:
        dt = datetime.strptime(shutdown_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return shutdown_at, len(shutdown_at)

    now = datetime.now(timezone.utc)
    past = dt <= now

    # Show human-friendly relative time
    diff = dt - now
    total_seconds = int(diff.total_seconds())
    if past:
        total_seconds = -total_seconds
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        if hours > 0:
            label = f"{hours}h{minutes}m ago"
        else:
            label = f"{minutes}m ago"
        label = f"OVERDUE ({label})"
    else:
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        if hours > 0:
            label = f"in {hours}h{minutes}m"
        else:
            label = f"in {minutes}m"

    raw_len = len(label)
    if past:
        label = click.style(label, fg="red", bold=True)
    return label, raw_len


@click.command("list")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "plain"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def list_cmd(output: str) -> None:
    """List workstation instances.

    Shows EC2 instances tagged Type=workstation with their instance ID,
    name, and state. Connect with: desk connect <name-or-id>

    AWS region and credential profile come from the environment
    (``AWS_REGION``, ``AWS_PROFILE``) or the desk config file.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    workstations = list_workstations(region=region, profile=profile)

    if not workstations:
        click.echo("No workstations found.")
        return

    if output == "plain":
        for w in workstations:
            shutdown_label, _ = _format_shutdown(w.shutdown_at, w.state)
            click.echo(
                f"{w.instance_id}\t{w.name}\t{_color_state(w.state)}\t{shutdown_label}"
            )
        return

    # Pre-compute shutdown labels so we can measure column width
    shutdown_labels: list[tuple[str, int]] = []
    for w in workstations:
        label, raw_len = _format_shutdown(w.shutdown_at, w.state)
        shutdown_labels.append((label, raw_len))

    # Table format
    max_id = max(len(w.instance_id) for w in workstations)
    max_name = max(len(w.name or "-") for w in workstations)
    max_state = max(len(w.state) for w in workstations)
    max_shutdown = max(raw for _, raw in shutdown_labels)
    max_id = max(max_id, 12)
    max_name = max(max_name, 4)
    max_state = max(max_state, 5)
    max_shutdown = max(max_shutdown, 8)

    header = (
        f"{'INSTANCE ID':<{max_id}}  {'NAME':<{max_name}}  "
        f"{'STATE':<{max_state}}  SHUTDOWN"
    )
    click.echo(header)
    click.echo("-" * len(header))

    for w, (shutdown_label, shutdown_raw_len) in zip(workstations, shutdown_labels):
        name = w.name or "-"
        state = _color_state(w.state)
        # Pad state by raw length (no ANSI) so columns align
        state_padding = " " * (max_state - len(w.state))
        click.echo(
            f"{w.instance_id:<{max_id}}  {name:<{max_name}}  "
            f"{state}{state_padding}  {shutdown_label}"
        )
