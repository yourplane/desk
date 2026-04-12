"""desk connect - SSH to a workstation over SSM tunnel."""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

import click

from desk.aws import add_temporary_ssh_key, is_ssm_ready, resolve_workstation
from desk.config import get_desk_settings
from desk.keys import get_default_private_key_path, get_public_key_content
from desk.log import get_logger

log = get_logger("connect")


def get_connection_argv(
    workstation: str,
    user: str,
    identity_file: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
    forward_agent: bool = False,
    remote_command: str | None = None,
    key_timeout: int = 300,
    verbose_callback: Callable[[str, float | None], None] | None = None,
    *,
    infra: bool = False,
) -> list[str]:
    """Resolve workstation, wait for SSM, inject public key via SSM, set AWS env, build SSH argv.

    Uses SSM to temporarily add the user's public key to the instance's authorized_keys
    (then remove it after key_timeout seconds). Uses identity_file if given, else default
    SSH key (~/.ssh/id_ed25519 or id_rsa). Caller can pass remote_command for e.g. screen.
    Returns argv suitable for os.execvp("ssh", argv).
    verbose_callback(message, elapsed_sec) is called at each step when provided (e.g. for -v).
    """
    def vb(msg: str, elapsed: float | None = None) -> None:
        if verbose_callback:
            verbose_callback(msg, elapsed)

    aws = get_desk_settings().aws_settings
    region = region or aws.region
    profile = profile or aws.profile

    log.debug("get_connection_argv workstation=%s region=%s profile=%s", workstation, region, profile)

    key_path = identity_file or get_default_private_key_path()
    if not key_path:
        raise click.ClickException(
            "No SSH key found. Create ~/.ssh/id_ed25519 (or id_rsa) or use -i PATH."
        )
    if not os.path.exists(key_path):
        raise click.ClickException(f"Key not found at {key_path}.")

    vb("get_connection_argv: resolve workstation")
    t0 = time.perf_counter()
    try:
        instance_id = resolve_workstation(
            workstation, region=region, profile=profile, infra=infra
        )
        log.info("resolved %s -> %s", workstation, instance_id)
    except ValueError as e:
        log.debug("resolve failed workstation=%s error=%s", workstation, e)
        raise click.UsageError(str(e)) from e
    vb("get_connection_argv: resolved", time.perf_counter() - t0)

    vb("get_connection_argv: is_ssm_ready")
    t1 = time.perf_counter()
    ssm_ready = is_ssm_ready(instance_id, region=region, profile=profile)
    log.debug("initial is_ssm_ready=%s", ssm_ready)
    vb("get_connection_argv: is_ssm_ready done", time.perf_counter() - t1)
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

    if region:
        os.environ["AWS_REGION"] = region
    if profile:
        os.environ["AWS_PROFILE"] = profile

    vb("get_connection_argv: get_public_key_content")
    t2 = time.perf_counter()
    try:
        public_key = get_public_key_content(key_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    vb("get_connection_argv: get_public_key_content done", time.perf_counter() - t2)

    key_settle_sleep = 0.5
    log.info("adding temporary SSH key to %s for %ds", instance_id, key_timeout)
    vb("get_connection_argv: add_temporary_ssh_key (SSM)")
    t3 = time.perf_counter()
    add_temporary_ssh_key(
        instance_id,
        user=user,
        public_key_content=public_key,
        timeout_seconds=key_timeout,
        region=region,
        profile=profile,
    )
    vb("get_connection_argv: add_temporary_ssh_key done", time.perf_counter() - t3)
    vb(f"get_connection_argv: sleep {key_settle_sleep}s (key settle)")
    time.sleep(key_settle_sleep)
    vb("get_connection_argv: sleep done", key_settle_sleep)

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
    ssh_args[1:1] = ["-i", key_path]

    for fwd in forwards:
        ssh_args[1:1] = ["-L", fwd]

    if forward_agent:
        ssh_args[1:1] = ["-A"]

    if remote_command:
        ssh_args.insert(-1, "-t")  # Force TTY so remote command (e.g. screen) gets a terminal
        ssh_args.append(remote_command)

    return ssh_args


@click.command("connect")
@click.argument("workstation")
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
    help="Path to SSH private key (default: ~/.ssh/id_ed25519 or id_rsa).",
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
    "-A",
    "--forward-agent",
    "forward_agent",
    is_flag=True,
    default=False,
    help="Forward authentication agent to the remote machine (same as ssh -A).",
)
@click.option(
    "--key-timeout",
    default=300,
    show_default=True,
    help="Seconds to keep the injected SSH key in authorized_keys before it is removed.",
)
@click.option(
    "--infra",
    is_flag=True,
    default=False,
    help="Target the managed router (Name=router, Type=router). Required to connect to the router.",
)
def connect(
    workstation: str,
    user: str,
    identity_file: str | None,
    wait: bool,
    wait_timeout: int,
    forwards: tuple[str, ...],
    forward_agent: bool,
    key_timeout: int,
    infra: bool,
) -> None:
    """Connect to a workstation via SSH over SSM tunnel.

    Injects your public key into the instance's authorized_keys via SSM (then
    removes it after --key-timeout). Uses ~/.ssh/id_ed25519 or id_rsa by default; -i to override.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.
    Use --infra to connect to the managed router instance (see ``desk list --infra``).
    Requires the Session Manager plugin and SSH client to be installed.

    AWS region and credential profile come from the environment or desk config.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    ssh_args = get_connection_argv(
        workstation=workstation,
        user=user,
        identity_file=identity_file,
        region=region,
        profile=profile,
        wait=wait,
        wait_timeout=wait_timeout,
        forwards=forwards,
        forward_agent=forward_agent,
        remote_command=None,
        key_timeout=key_timeout,
        infra=infra,
    )
    log.info("exec ssh user=%s", user)
    try:
        os.execvp("ssh", ssh_args)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(127)
