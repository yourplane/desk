"""desk list - list workstation instances."""

from __future__ import annotations

import os

import click

from desk.aws import Workstation, list_workstations


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.command("list")
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
    "--output",
    "-o",
    type=click.Choice(["table", "plain"]),
    default="table",
    show_default=True,
    help="Output format.",
)
def list_cmd(
    region: str | None,
    profile: str | None,
    output: str,
) -> None:
    """List workstation instances.

    Shows EC2 instances tagged Type=workstation with their instance ID,
    name, and state. Connect with: desk connect <name-or-id>
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    workstations = list_workstations(region=region, profile=profile)

    if not workstations:
        click.echo("No workstations found.")
        return

    if output == "plain":
        for w in workstations:
            click.echo(f"{w.instance_id}\t{w.name}\t{w.state}")
        return

    # Table format
    max_id = max(len(w.instance_id) for w in workstations)
    max_name = max(len(w.name or "-") for w in workstations)
    max_id = max(max_id, 12)
    max_name = max(max_name, 4)

    header = f"{'INSTANCE ID':<{max_id}}  {'NAME':<{max_name}}  STATE"
    click.echo(header)
    click.echo("-" * len(header))

    for w in workstations:
        name = w.name or "-"
        click.echo(f"{w.instance_id:<{max_id}}  {name:<{max_name}}  {w.state}")
