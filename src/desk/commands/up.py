"""desk up - create a workstation and connect to it."""

from __future__ import annotations

import sys
import time

import click

from desk.aws import (
    compute_shutdown_at,
    get_instance_state,
    list_workstations,
    parse_duration,
    resolve_workstation,
    set_shutdown_tag,
    start_instance,
)
from desk.commands import connect, create
from desk.config import get_default_profile, get_default_region
from desk.keys import get_default_private_key_path


@click.command("up")
@click.argument("workstation")
@click.option(
    "--instance-type",
    "-t",
    default="t3.medium",
    show_default=True,
    help="EC2 instance type.",
)
@click.option(
    "--ami",
    "-a",
    default=None,
    help="AMI ID. Default: latest AMI matching config ami_prefix, or latest Ubuntu 24.04 LTS.",
)
@click.option(
    "--stack",
    "-s",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk VPC.",
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
@click.option(
    "--user",
    "-u",
    default="ubuntu",
    show_default=True,
    help="SSH username on the instance.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for instance to be ready if SSM agent not yet connected.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM before failing.",
)
@click.option(
    "--forward",
    "-L",
    "forwards",
    multiple=True,
    help="Port forward in SSH -L format: [local_port:]remote_host:remote_port. Can be repeated.",
)
@click.option(
    "--shutdown",
    "shutdown_after",
    type=str,
    default="4h",
    show_default=True,
    help="Duration until auto-stop, e.g. 4h, 30m, 2h30m (0 to disable).",
)
def up(
    workstation: str,
    instance_type: str,
    ami: str | None,
    stack: str,
    region: str | None,
    profile: str | None,
    user: str,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
    shutdown_after: str,
) -> None:
    """Create a workstation and connect to it.

    If a workstation with the target name already exists (running or pending),
    skips create and connects. If stopped or stopping, starts and connects.
    Otherwise creates then connects.
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()
    ctx = click.get_current_context()
    try:
        resolve_workstation(workstation, region=region, profile=profile)
    except ValueError as e:
        if "not found" not in str(e).lower():
            raise click.UsageError(str(e)) from e
        # No running/pending workstation with this name
        # Check if there's a stopped/stopping one to start
        stopped = [
            w for w in list_workstations(
                region=region, profile=profile, states=["stopped", "stopping"]
            )
            if w.name == workstation
        ]
        if stopped:
            ws = stopped[0]
            instance_id = ws.instance_id
            if ws.state == "stopping":
                # Wait for instance to stop with spinner
                spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
                idx = 0
                deadline = time.monotonic() + wait_timeout
                err = sys.stderr
                while time.monotonic() < deadline:
                    state = get_instance_state(instance_id, region=region, profile=profile)
                    if state == "stopped":
                        err.write("\r" + " " * 50 + "\r")
                        err.flush()
                        break
                    char = spinner[idx % len(spinner)]
                    err.write(f"\r{char} Waiting for {instance_id} to stop... ")
                    err.flush()
                    idx += 1
                    time.sleep(0.5)
                else:
                    err.write("\n")
                    err.flush()
                    raise click.ClickException(
                        f"Timeout waiting for {instance_id} to stop. Try again later."
                    )
            click.echo(f"Starting {instance_id}...")
            start_instance(instance_id, region=region, profile=profile)
            shutdown_hours = parse_duration(shutdown_after)
            if shutdown_hours > 0:
                shutdown_time = compute_shutdown_at(shutdown_hours)
                set_shutdown_tag(instance_id, shutdown_time, region=region, profile=profile)
            click.secho("Started.", fg="green")
        else:
            # No existing workstation at all, create it
            ctx.invoke(
                create.create,
                workstation=workstation,
                instance_type=instance_type,
                ami=ami,
                stack=stack,
                region=region,
                profile=profile,
                shutdown_after=shutdown_after,
            )
    else:
        click.echo(f"Workstation '{workstation}' already exists. Connecting...")

    key_path = get_default_private_key_path()
    if not key_path:
        click.echo(
            "No SSH key found. Create ~/.ssh/id_ed25519 (or id_rsa), then run:",
            err=True,
        )
        click.echo(f"  desk connect {workstation}", err=True)
        return

    ctx.invoke(
        connect.connect,
        workstation=workstation,
        user=user,
        identity_file=None,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
        forwards=forwards,
    )
