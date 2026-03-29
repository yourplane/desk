"""desk create - launch a new workstation instance."""

from __future__ import annotations

import click

from desk.aws import create_workstation
from desk.config import get_desk_settings


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
    shutdown_after: str,
) -> None:
    """Create a new workstation instance.

    Launches an EC2 instance in the desk VPC with SSM support for
    SSH-over-Session-Manager connectivity. The instance is tagged for
    desk discovery (Type=workstation).

    Requires the desk CloudFormation stack to be deployed first.

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    click.echo(f"Launching instance '{workstation}' ({instance_type})...")
    try:
        instance_id, _ = create_workstation(
            workstation,
            instance_type,
            ami_id=ami or None,
            shutdown_after=shutdown_after,
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

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
