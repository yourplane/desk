"""desk kill - terminate a workstation instance."""

from __future__ import annotations

import os

import click

from desk.aws import list_workstations, resolve_workstation, terminate_instance
from desk.config import get_desk_settings
from desk.router_infra import is_router_instance_ops_enabled
from desk.web_routes import clear_ports


@click.command("kill")
@click.argument("workstation", required=True)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
@click.option(
    "--infra",
    is_flag=True,
    default=False,
    help="Target the managed router (Type=router).",
)
def kill(
    workstation: str,
    yes: bool,
    infra: bool,
) -> None:
    """Terminate a workstation instance.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Use --infra to terminate the managed router (the ASG will typically launch a replacement).

    This permanently destroys the instance and all data on its root volume.

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = resolve_workstation(
            workstation,
            region=region,
            profile=profile,
            states=["pending", "running", "stopping", "stopped"],
            infra=infra,
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if infra and not is_router_instance_ops_enabled(region=region, profile=profile):
        raise click.UsageError(
            "Router instance operations are disabled while the active stack is absent."
        )

    if not yes:
        click.confirm(
            f"Terminate {instance_id}? This cannot be undone.",
            abort=True,
        )

    click.echo(f"Terminating {instance_id}...")
    terminate_instance(instance_id, region=region, profile=profile)
    if not infra:
        ws_name = workstation
        if workstation.startswith("i-"):
            for w in list_workstations(
                region=region,
                profile=profile,
                states=["pending", "running", "stopping", "stopped"],
            ):
                if w.instance_id == instance_id and w.name:
                    ws_name = w.name
                    break
        try:
            clear_ports(ws_name)
        except Exception:
            pass
    click.secho("Terminated.", fg="red")
