"""desk create - launch a new workstation instance."""

from __future__ import annotations

import click

from desk.aws import (
    DeskVpcOutputs,
    get_desk_vpc_outputs,
    get_latest_ubuntu_ami,
    run_instance,
)


def _get_region() -> str | None:
    """Resolve region from env or config."""
    import os
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    import os
    return os.environ.get("AWS_PROFILE")


@click.command("create")
@click.option(
    "--name",
    "-n",
    required=True,
    help="Name for the workstation (used as EC2 Name tag and alias).",
)
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
    help="AMI ID. Default: latest Ubuntu 24.04 LTS.",
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
def create(
    name: str,
    instance_type: str,
    ami: str | None,
    stack: str,
    region: str | None,
    profile: str | None,
) -> None:
    """Create a new workstation instance.

    Launches an EC2 instance in the desk VPC with SSM support for
    SSH-over-Session-Manager connectivity. The instance is tagged for
    desk discovery (Type=workstation).

    Requires the desk CloudFormation stack to be deployed first.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    click.echo("Fetching desk VPC configuration...")
    vpc_outputs: DeskVpcOutputs = get_desk_vpc_outputs(
        stack_name=stack,
        region=region,
        profile=profile,
    )

    if ami:
        click.echo(f"Using specified AMI: {ami}")
    else:
        click.echo("Looking up latest Ubuntu 24.04 LTS AMI...")
        ami = get_latest_ubuntu_ami(region=region, profile=profile)

    # Use first private subnet
    subnet_id = vpc_outputs.private_subnet_ids[0]

    click.echo(f"Launching instance '{name}' ({instance_type})...")
    instance_id = run_instance(
        ami_id=ami,
        instance_type=instance_type,
        subnet_id=subnet_id,
        security_group_ids=[vpc_outputs.security_group_id],
        iam_instance_profile_name=vpc_outputs.instance_profile_name,
        name=name,
        region=region,
        profile=profile,
    )

    click.echo()
    click.secho("Workstation created successfully!", fg="green", bold=True)
    click.echo()
    click.echo(f"  Instance ID:  {instance_id}")
    click.echo(f"  Name:        {name}")
    click.echo(f"  State:       pending (initializing)")
    click.echo()
    click.echo("Connect once the instance is running:")
    click.echo(f"  desk connect {name}")
    click.echo(f"  desk connect {instance_id}")
