"""Synchronous `desk ami create` / `desk ami list` (non-async pipeline)."""

from __future__ import annotations

import time
from datetime import datetime

import click

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim


def run_ami_list(output: str, show_all: bool) -> None:
    """List AMIs (desk-managed or all)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    amis = aws_shim.list_amis(region=region, profile=profile, managed_only=not show_all)

    if not amis:
        click.echo("No AMIs found.")
        return

    if output == "plain":
        for a in amis:
            source = a.source_instance or "-"
            click.echo(f"{a.image_id}\t{a.name}\t{a.state}\t{a.creation_date}\t{source}")
        return

    max_id = max(len(a.image_id) for a in amis)
    max_name = max(len(a.name) for a in amis)
    max_state = max(len(a.state) for a in amis)
    max_date = max(len(a.creation_date) for a in amis)
    max_source = max(len(a.source_instance or "-") for a in amis)
    max_id = max(max_id, 9)
    max_name = max(max_name, 4)
    max_state = max(max_state, 5)
    max_date = max(max_date, 7)
    max_source = max(max_source, 7)

    header = (
        f"{'IMAGE ID':<{max_id}}  {'NAME':<{max_name}}  "
        f"{'STATE':<{max_state}}  {'CREATED':<{max_date}}  SOURCE"
    )
    click.echo(header)
    click.echo("-" * len(header))

    for a in amis:
        source = a.source_instance or "-"
        click.echo(
            f"{a.image_id:<{max_id}}  {a.name:<{max_name}}  "
            f"{a.state:<{max_state}}  {a.creation_date:<{max_date}}  {source}"
        )


def run_ami_create(
    workstation: str,
    name: str | None,
    description: str | None,
    no_reboot: bool,
    wait: bool,
    timeout: int,
) -> None:
    """Create an AMI from a workstation instance."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        instance_id = aws_shim.resolve_workstation(
            workstation,
            region=region,
            profile=profile,
            states=["pending", "running", "stopping", "stopped"],
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    if not name:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_name = (
            workstation if not workstation.startswith("i-") else f"workstation-{workstation}"
        )
        name = f"{base_name}-{timestamp}"

    click.echo(f"Creating AMI from {instance_id}...")

    state = aws_shim.get_instance_state(instance_id, region=region, profile=profile)
    if state == "stopping":
        click.echo("Instance is stopping, waiting for it to stop...")
        if not aws_shim.wait_for_instance_state(
            instance_id, "stopped", region=region, profile=profile, timeout=300
        ):
            raise click.ClickException("Timed out waiting for instance to stop.")
        state = "stopped"

    if state == "pending":
        click.echo("Instance is starting, waiting for it to run...")
        if not aws_shim.wait_for_instance_state(
            instance_id, "running", region=region, profile=profile, timeout=300
        ):
            raise click.ClickException("Timed out waiting for instance to start.")
        state = "running"

    if no_reboot:
        click.echo("Using --no-reboot: filesystem may be in inconsistent state.")
    elif state == "running":
        click.echo("Instance will be rebooted during AMI creation.")

    image_id = aws_shim.create_ami(
        instance_id=instance_id,
        name=name,
        description=description,
        no_reboot=no_reboot,
        region=region,
        profile=profile,
    )

    click.echo(f"AMI creation started: {image_id}")
    click.echo(f"  Name: {name}")

    if not wait:
        click.echo()
        click.echo("AMI is being created in the background.")
        click.echo(f"Check status: aws ec2 describe-images --image-ids {image_id}")
        return

    click.echo()
    click.echo("Waiting for AMI to become available...")

    start_time = time.monotonic()
    poll_interval = 10.0
    last_state = None

    while time.monotonic() - start_time < timeout:
        state = aws_shim.get_ami_state(image_id, region=region, profile=profile)
        if state != last_state:
            if state == "pending":
                click.echo("  Status: pending (creating snapshot and registering image)")
            elif state:
                click.echo(f"  Status: {state}")
            last_state = state

        if state == "available":
            elapsed = int(time.monotonic() - start_time)
            click.echo()
            click.secho("AMI created successfully!", fg="green", bold=True)
            click.echo()
            click.echo(f"  AMI ID:  {image_id}")
            click.echo(f"  Name:    {name}")
            click.echo(f"  Time:    {elapsed}s")
            click.echo()
            click.echo("Use this AMI with:")
            click.echo(f"  desk create --ami {image_id}")
            return

        if state in ("failed", "error", "deregistered"):
            raise click.ClickException(f"AMI creation failed with state: {state}")

        time.sleep(poll_interval)

    raise click.ClickException(
        f"Timed out waiting for AMI to become available after {timeout}s. "
        f"AMI {image_id} may still be creating - check AWS console."
    )
