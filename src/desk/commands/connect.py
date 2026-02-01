"""desk connect - SSH to a workstation over SSM tunnel."""

from __future__ import annotations

import os
import sys
import time

import click

from desk.aws import is_ssm_ready, resolve_workstation
from desk.keys import get_key_path
from desk.log import get_logger

log = get_logger("connect")


def _get_region() -> str | None:
    """Resolve region from env or config."""
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def _get_profile() -> str | None:
    """Resolve profile from env."""
    return os.environ.get("AWS_PROFILE")


@click.command("connect")
@click.argument("workstation", default="main")
@click.option(
    "--user",
    "-u",
    default="ubuntu",
    show_default=True,
    help="SSH username on the instance.",
)
@click.option(
    "--identity",
    "-i",
    "identity_file",
    default=None,
    help="Path to SSH private key.",
)
@click.option(
    "--key",
    "-k",
    "key_name",
    default="main-key",
    show_default=True,
    help="Desk-managed key name (from desk key create). Resolves to ~/.config/desk/keys/<name>.pem",
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
def connect(
    workstation: str,
    user: str,
    identity_file: str | None,
    key_name: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Connect to a workstation via SSH over SSM tunnel.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Requires the Session Manager plugin and SSH client to be installed.

    The instance must have an SSH key associated. Use -i for key path or --key for desk-managed keys.
    """
    region = region or _get_region()
    profile = profile or _get_profile()

    log.debug("connect workstation=%s region=%s profile=%s", workstation, region, profile)

    # Resolve identity: -i takes precedence over --key
    if identity_file:
        key_path = identity_file
    elif key_name:
        key_path = get_key_path(key_name)
        if not os.path.exists(key_path):
            raise click.ClickException(
                f"Key '{key_name}' not found at {key_path}. "
                "Create it with: desk key create " + key_name
            )
    else:
        key_path = None

    try:
        instance_id = resolve_workstation(workstation, region=region, profile=profile)
        log.info("resolved %s -> %s", workstation, instance_id)
    except ValueError as e:
        log.debug("resolve failed workstation=%s error=%s", workstation, e)
        raise click.UsageError(str(e)) from e

    # Wait for SSM agent if not yet ready
    ssm_ready = is_ssm_ready(instance_id, region=region, profile=profile)
    log.debug("initial is_ssm_ready=%s", ssm_ready)
    if wait and not ssm_ready:
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        idx = 0
        deadline = time.monotonic() + wait_timeout
        err = sys.stderr
        log.info("waiting for SSM ready instance_id=%s timeout=%ds", instance_id, wait_timeout)
        while time.monotonic() < deadline:
            if is_ssm_ready(instance_id, region=region, profile=profile):
                elapsed = time.monotonic() - (deadline - wait_timeout)
                log.info("SSM ready after %.1fs", elapsed)
                err.write("\r" + " " * 50 + "\r")
                err.flush()
                break
            char = spinner[idx % len(spinner)]
            err.write(f"\r{char} Waiting for instance to be ready... ")
            err.flush()
            idx += 1
            if idx % 10 == 0:
                log.debug("SSM wait poll %d elapsed=%.1fs", idx, time.monotonic() - (deadline - wait_timeout))
            time.sleep(0.5)
        else:
            err.write("\n")
            err.flush()
            log.warning("SSM wait timed out instance_id=%s", instance_id)
            raise click.ClickException(
                f"Instance {instance_id} did not become ready within {wait_timeout}s. "
                "Check that the instance is running and has the SSM agent."
            )

    # Set AWS env so ProxyCommand inherits them
    if region:
        os.environ["AWS_REGION"] = region
    if profile:
        os.environ["AWS_PROFILE"] = profile

    proxy_cmd = (
        "sh -c \"aws ssm start-session --target %h "
        "--document-name AWS-StartSSHSession --parameters 'portNumber=%p'\""
    )

    ssh_args = [
        "ssh",
        "-o",
        f"ProxyCommand={proxy_cmd}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{user}@{instance_id}",
    ]
    if key_path:
        ssh_args[1:1] = ["-i", key_path]

    log.info("exec ssh user=%s instance_id=%s", user, instance_id)
    # Replace our process with ssh for proper terminal handling
    try:
        os.execvp("ssh", ssh_args)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(127)
