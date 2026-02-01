"""desk up - create a workstation and connect to it."""

from __future__ import annotations

import click

from desk.aws import resolve_workstation
from desk.commands import connect, create


@click.command("up")
@click.option(
    "--name",
    "-n",
    default="main",
    show_default=True,
    help="Workstation name (used for create and connect).",
)
@click.option(
    "--key",
    "-k",
    "key_name",
    default="main-key",
    show_default=True,
    help="EC2 key pair name for create and SSH for connect.",
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
def up(
    name: str,
    key_name: str,
    instance_type: str,
    ami: str | None,
    stack: str,
    region: str | None,
    profile: str | None,
    user: str,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Create a workstation and connect to it.

    If a workstation with the target name already exists (running or pending),
    skips create and connects. Stopped instances with that name are ignored.
    Otherwise creates then connects. Uses the same defaults (main, main-key).
    """
    ctx = click.get_current_context()
    try:
        resolve_workstation(name, region=region, profile=profile)
    except ValueError as e:
        if "not found" not in str(e).lower():
            raise click.UsageError(str(e)) from e
        # No running/pending workstation with this name, create it
        ctx.invoke(
            create.create,
            name=name,
            instance_type=instance_type,
            ami=ami,
            key_name=key_name,
            stack=stack,
            region=region,
            profile=profile,
        )
    else:
        click.echo(f"Workstation '{name}' already exists. Connecting...")

    ctx.invoke(
        connect.connect,
        workstation=name,
        user=user,
        identity_file=None,
        key_name=key_name,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
    )
