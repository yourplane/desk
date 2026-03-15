"""desk create - launch a new workstation instance."""

from __future__ import annotations

import click

from desk.aws import (
    DeskVpcOutputs,
    compute_shutdown_at,
    get_desk_vpc_outputs,
    get_latest_ami_by_name_prefix,
    get_latest_ubuntu_ami,
    list_workstations,
    parse_duration,
    run_instance,
    set_shutdown_tag,
)
from desk.config import get_default_ami_prefix, get_default_profile, get_default_region


@click.command("create")
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
    "--shutdown",
    "shutdown_after",
    type=str,
    default="4h",
    show_default=True,
    help="Duration until auto-stop, e.g. 4h, 30m, 2h30m (0 to disable).",
)
def create(
    workstation: str,
    instance_type: str,
    ami: str | None,
    stack: str,
    region: str | None,
    profile: str | None,
    shutdown_after: str,
) -> None:
    """Create a new workstation instance.

    Launches an EC2 instance in the desk VPC with SSM support for
    SSH-over-Session-Manager connectivity. The instance is tagged for
    desk discovery (Type=workstation).

    Requires the desk CloudFormation stack to be deployed first.
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    # Check for duplicate workstation names (only terminated state allows duplicates)
    existing = list_workstations(region=region, profile=profile)
    duplicates = [
        w for w in existing
        if w.name == workstation and w.state != "terminated"
    ]
    if duplicates:
        states = ", ".join(f"{w.instance_id} ({w.state})" for w in duplicates)
        raise click.ClickException(
            f"Workstation named '{workstation}' already exists: {states}. "
            "Use a different name or terminate the existing workstation first."
        )

    click.echo("Fetching desk VPC configuration...")
    vpc_outputs: DeskVpcOutputs = get_desk_vpc_outputs(
        stack_name=stack,
        region=region,
        profile=profile,
    )

    if ami:
        click.echo(f"Using specified AMI: {ami}")
    else:
        ami_prefix = get_default_ami_prefix()
        if ami_prefix:
            click.echo(f"Looking up latest AMI with name prefix '{ami_prefix}'...")
            ami = get_latest_ami_by_name_prefix(ami_prefix, region=region, profile=profile)
        else:
            ami = None
        if not ami:
            click.echo("Looking up latest Ubuntu 24.04 LTS AMI...")
            ami = get_latest_ubuntu_ami(region=region, profile=profile)

    # Use first private subnet
    subnet_id = vpc_outputs.private_subnet_ids[0]

    click.echo(f"Launching instance '{workstation}' ({instance_type})...")
    instance_id = run_instance(
        ami_id=ami,
        instance_type=instance_type,
        subnet_id=subnet_id,
        security_group_ids=[vpc_outputs.security_group_id],
        iam_instance_profile_name=vpc_outputs.instance_profile_name,
        name=workstation,
        key_name=None,
        region=region,
        profile=profile,
    )

    shutdown_hours = parse_duration(shutdown_after)
    if shutdown_hours > 0:
        shutdown_time = compute_shutdown_at(shutdown_hours)
        set_shutdown_tag(instance_id, shutdown_time, region=region, profile=profile)

    click.echo()
    click.secho("Workstation created successfully!", fg="green", bold=True)
    click.echo()
    click.echo(f"  Instance ID:  {instance_id}")
    click.echo(f"  Name:        {workstation}")
    click.echo(f"  State:       pending (initializing)")
    click.echo()
    click.echo("Connect once the instance is running:")
    click.echo(f"  desk connect {workstation}")
    click.echo(f"  desk connect {instance_id}")
