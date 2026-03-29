"""desk scp - Copy files to/from a workstation over SSM tunnel."""

from __future__ import annotations

import os
import sys
import time

import click

from desk.aws import add_temporary_ssh_key, is_ssm_ready, resolve_workstation
from desk.config import get_desk_settings
from desk.keys import get_default_private_key_path, get_public_key_content
from desk.log import get_logger

log = get_logger("scp")


def _remote_workstation_from_path(path: str) -> str | None:
    """If path looks like 'workstation_name:remote_path', return the workstation name; else None.

    Avoids treating Windows paths (C:\\...) or relative paths with colons as workstation refs.
    """
    if ":" not in path or path.startswith(":"):
        return None
    prefix, suffix = path.split(":", 1)
    # Workstation name: no slash, and remote path typically starts with / or ~
    if not prefix or "/" in prefix or "\\" in prefix:
        return None
    if suffix.startswith("/") or suffix.startswith("~"):
        return prefix
    # e.g. host:relative/path - still treat as workstation:path
    if prefix and len(prefix) <= 64 and not prefix.endswith("."):
        return prefix
    return None


def _parse_scp_path(path: str, workstation: str, user: str, instance_id: str) -> str:
    """Parse an SCP path, expanding workstation references.

    Supports formats:
    - Local path: /path/to/file or ./file or relative/path
    - Remote path with workstation: workstation:/remote/path or workstation:relative/path
    - Remote path with colon: :/remote/path (uses default workstation)

    Returns the path formatted for scp (local paths unchanged, remote as user@instance_id:path).
    """
    if path.startswith(":"):
        # :/path means remote on the default workstation
        remote_path = path[1:]
        return f"{user}@{instance_id}:{remote_path}"

    if ":" in path:
        prefix, suffix = path.split(":", 1)
        if prefix == workstation or prefix == "":
            return f"{user}@{instance_id}:{suffix}"
        # workstation name in path overrides default (caller must resolve that workstation)
        if _remote_workstation_from_path(path):
            return f"{user}@{instance_id}:{suffix}"

    # Local path - return as-is
    return path


@click.command("scp")
@click.argument("workstation")
@click.argument("source")
@click.argument("destination")
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
    "--recursive",
    "-r",
    "recursive",
    is_flag=True,
    default=False,
    help="Recursively copy directories.",
)
def scp(
    workstation: str,
    source: str,
    destination: str,
    user: str,
    identity_file: str | None,
    wait: bool,
    wait_timeout: int,
    recursive: bool,
) -> None:
    """Copy files to/from a workstation via SCP over SSM tunnel.

    Use workstation:path or :path to refer to remote paths on the workstation.
    Local paths are specified without a prefix.

    \b
    Examples:
      desk scp main ./local-file.txt :~/remote-file.txt      # Upload to home dir
      desk scp main :~/remote-file.txt ./local-file.txt     # Download from home dir
      desk scp main -r ./local-dir :~/remote-dir            # Upload directory recursively
      desk scp main :/etc/hosts ./hosts                     # Download from remote path
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    log.debug(
        "scp source=%s dest=%s workstation=%s region=%s profile=%s",
        source,
        destination,
        workstation,
        region,
        profile,
    )

    key_path = identity_file or get_default_private_key_path()
    if not key_path:
        raise click.ClickException(
            "No SSH key found. Create ~/.ssh/id_ed25519 (or id_rsa) or use -i PATH."
        )
    if not os.path.exists(key_path):
        raise click.ClickException(f"Key not found at {key_path}.")

    # If source or destination is "workstation_name:path", use that workstation
    for path in (source, destination):
        ws_from_path = _remote_workstation_from_path(path)
        if ws_from_path:
            workstation = ws_from_path
            break

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

    try:
        public_key = get_public_key_content(key_path)
    except (FileNotFoundError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    add_temporary_ssh_key(
        instance_id,
        user=user,
        public_key_content=public_key,
        timeout_seconds=300,
        region=region,
        profile=profile,
    )
    time.sleep(1.5)

    # Parse source and destination paths
    scp_source = _parse_scp_path(source, workstation, user, instance_id)
    scp_dest = _parse_scp_path(destination, workstation, user, instance_id)

    proxy_cmd = (
        "sh -c \"aws ssm start-session --target %h "
        "--document-name AWS-StartSSHSession --parameters 'portNumber=%p'\""
    )

    scp_args = [
        "scp",
        "-o",
        f"ProxyCommand={proxy_cmd}",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]

    scp_args.extend(["-i", key_path])

    if recursive:
        scp_args.append("-r")

    scp_args.extend([scp_source, scp_dest])

    log.info("exec scp source=%s dest=%s", scp_source, scp_dest)
    # Replace our process with scp for proper terminal handling
    try:
        os.execvp("scp", scp_args)
    except OSError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(127)
