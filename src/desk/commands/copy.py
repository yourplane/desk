"""desk copy - Copy files between local, workstation, and S3 using SSM where needed."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum

import click

from desk.aws import (
    add_temporary_ssh_key,
    get_command_invocation,
    get_desk_copy_bucket,
    is_ssm_ready,
    resolve_workstation,
    send_ssm_command,
    wait_for_ssm_ready,
)
from desk.config import get_default_profile, get_default_region
from desk.keys import get_default_private_key_path, get_public_key_content
from desk.log import get_logger

log = get_logger("copy")


class LocationKind(Enum):
    LOCAL = "local"
    WORKSTATION = "workstation"
    S3 = "s3"


@dataclass(frozen=True)
class Location:
    """Parsed copy location: local path, workstation name + path, or S3 key."""

    kind: LocationKind
    path: str  # local path, or remote path, or S3 key
    workstation_name: str | None = None  # only for WORKSTATION

    def __repr__(self) -> str:
        if self.kind == LocationKind.WORKSTATION and self.workstation_name:
            return f"Location({self.kind.value}, {self.workstation_name!r}:{self.path!r})"
        if self.kind == LocationKind.S3:
            return f"Location(s3, s3:/{self.path})"
        return f"Location({self.kind.value}, {self.path!r})"


def parse_location(s: str) -> Location:
    """Parse a copy location string into a Location.

    - S3: "s3:/key" or "s3:/path/to/key" (leading slash after colon = desk bucket).
      Disambiguates from a workstation named "s3" (use "s3:path" for that).
    - Workstation: "name:path" (name has no slash; no default workstation, so
      "name" must be non-empty).
    - Local: any other path (e.g. ./file, /tmp/dir, relative/path).

    Rejects ":path" (default workstation not supported).
    """
    s = s.strip()
    if not s:
        raise ValueError("Empty location")

    # Default workstation not supported
    if s.startswith(":"):
        raise ValueError(
            "Default workstation not supported. Use workstation_name:path (e.g. main:/path)."
        )

    # S3: s3:/key (leading slash after colon)
    if s.startswith("s3:/"):
        key = s[4:].lstrip("/")  # normalize to no leading slash
        return Location(LocationKind.S3, key or "")

    if s.startswith("s3:"):
        # "s3:path" without slash = workstation named "s3" with path "path"
        return Location(
            LocationKind.WORKSTATION,
            s[3:],
            workstation_name="s3",
        )

    # Workstation: name:path (name has no / or \)
    # Windows drive (e.g. C:\path) = local: single letter + suffix starting with \ or /
    if ":" in s:
        prefix, suffix = s.split(":", 1)
        if (
            len(prefix) == 1
            and prefix.isalpha()
            and (suffix.startswith("\\") or suffix.startswith("/"))
        ):
            return Location(LocationKind.LOCAL, s)
        if prefix and "/" not in prefix and "\\" not in prefix and len(prefix) <= 64:
            return Location(
                LocationKind.WORKSTATION,
                suffix,
                workstation_name=prefix,
            )

    # Local
    return Location(LocationKind.LOCAL, s)


def _reject_local_to_local(src: Location, dest: Location) -> None:
    if src.kind == LocationKind.LOCAL and dest.kind == LocationKind.LOCAL:
        raise click.ClickException(
            "Copy between two local paths is not supported. Use cp or copy locally."
        )


def _reject_workstation_to_workstation(src: Location, dest: Location) -> None:
    if src.kind == LocationKind.WORKSTATION and dest.kind == LocationKind.WORKSTATION:
        raise click.ClickException(
            "Copy between two workstations is not supported. "
            "Copy to local or s3 first, then to the other workstation."
        )


def _scp_style_remote(workstation_name: str, path: str, user: str, instance_id: str) -> str:
    """Format workstation location as for scp: user@instance_id:path."""
    return f"{user}@{instance_id}:{path}"


def _copy_local_to_workstation(
    local_path: str,
    workstation_name: str,
    remote_path: str,
    *,
    recursive: bool,
    user: str,
    key_path: str,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Copy from local to workstation via SCP over SSM."""
    instance_id = resolve_workstation(workstation_name, region=region, profile=profile)
    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
    if region:
        os.environ["AWS_REGION"] = region
    if profile:
        os.environ["AWS_PROFILE"] = profile or ""
    public_key = get_public_key_content(key_path)
    add_temporary_ssh_key(
        instance_id,
        user=user,
        public_key_content=public_key,
        timeout_seconds=300,
        region=region,
        profile=profile,
    )
    time.sleep(1.5)
    remote_str = _scp_style_remote(workstation_name, remote_path, user, instance_id)
    proxy_cmd = (
        'sh -c "aws ssm start-session --target %h '
        "--document-name AWS-StartSSHSession --parameters 'portNumber=%p'\""
    )
    args = [
        "scp",
        "-o",
        f"ProxyCommand={proxy_cmd}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        key_path,
    ]
    if recursive:
        args.append("-r")
    args.extend([local_path, remote_str])
    log.debug("scp %s", args)
    r = subprocess.run(args)
    if r.returncode != 0:
        raise click.ClickException(f"scp failed with exit code {r.returncode}")


def _copy_workstation_to_local(
    workstation_name: str,
    remote_path: str,
    local_path: str,
    *,
    recursive: bool,
    user: str,
    key_path: str,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Copy from workstation to local via SCP over SSM."""
    instance_id = resolve_workstation(workstation_name, region=region, profile=profile)
    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
    if region:
        os.environ["AWS_REGION"] = region
    if profile:
        os.environ["AWS_PROFILE"] = profile or ""
    public_key = get_public_key_content(key_path)
    add_temporary_ssh_key(
        instance_id,
        user=user,
        public_key_content=public_key,
        timeout_seconds=300,
        region=region,
        profile=profile,
    )
    time.sleep(1.5)
    remote_str = _scp_style_remote(workstation_name, remote_path, user, instance_id)
    proxy_cmd = (
        'sh -c "aws ssm start-session --target %h '
        "--document-name AWS-StartSSHSession --parameters 'portNumber=%p'\""
    )
    args = [
        "scp",
        "-o",
        f"ProxyCommand={proxy_cmd}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        key_path,
    ]
    if recursive:
        args.append("-r")
    args.extend([remote_str, local_path])
    log.debug("scp %s", args)
    r = subprocess.run(args)
    if r.returncode != 0:
        raise click.ClickException(f"scp failed with exit code {r.returncode}")


def _copy_local_to_s3(
    local_path: str,
    bucket: str,
    key: str,
    *,
    recursive: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Upload from local to desk S3 bucket."""
    import boto3

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    if recursive and os.path.isdir(local_path):
        for root, _dirs, files in os.walk(local_path):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_path)
                k = f"{key.rstrip('/')}/{rel}" if key else rel
                s3.upload_file(full, bucket, k)
                log.debug("upload %s -> s3://%s/%s", full, bucket, k)
    else:
        k = key.rstrip("/") or os.path.basename(local_path)
        s3.upload_file(local_path, bucket, k)
        log.debug("upload %s -> s3://%s/%s", local_path, bucket, k)


def _copy_s3_to_local(
    bucket: str,
    key: str,
    local_path: str,
    *,
    recursive: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Download from desk S3 bucket to local."""
    import boto3

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    if recursive:
        paginator = s3.get_paginator("list_objects_v2")
        prefix = key.rstrip("/") + "/" if key else ""
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents") or []:
                k = obj["Key"]
                if k.endswith("/"):
                    continue
                rel = k[len(prefix) :] if prefix else k
                local_file = os.path.join(local_path, rel)
                os.makedirs(os.path.dirname(local_file), exist_ok=True)
                s3.download_file(bucket, k, local_file)
                log.debug("download s3://%s/%s -> %s", bucket, k, local_file)
    else:
        # Ensure parent dir exists when local_path includes a path
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        s3.download_file(bucket, key, local_path)
        log.debug("download s3://%s/%s -> %s", bucket, key, local_path)


def _run_ssm_command_and_wait(
    instance_id: str,
    command: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Send SSM command and wait for completion; raise on failure."""
    command_id = send_ssm_command(
        instance_id,
        command,
        region=region,
        profile=profile,
        timeout_seconds=3600,
    )
    terminal = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    for _ in range(3600):  # up to 1 hour
        time.sleep(1)
        try:
            result = get_command_invocation(
                command_id, instance_id, region=region, profile=profile
            )
        except Exception as e:
            log.debug("get_command_invocation: %s", e)
            continue
        if result.status in terminal:
            if result.stdout:
                click.echo(result.stdout, nl=False)
            if result.stderr:
                click.echo(result.stderr, nl=False, err=True)
            if result.status != "Success":
                raise click.ClickException(
                    f"Command failed: {result.status}"
                    + (f" (exit {result.exit_code})" if result.exit_code is not None else "")
                )
            return


def _copy_workstation_to_s3(
    workstation_name: str,
    remote_path: str,
    bucket: str,
    key: str,
    *,
    recursive: bool,
    user: str,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Copy from workstation to S3 via SSM (aws s3 cp/sync on instance)."""
    instance_id = resolve_workstation(workstation_name, region=region, profile=profile)
    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
    region_str = region or "us-east-1"
    dest = f"s3://{bucket}/{key.rstrip('/')}"
    if recursive:
        cmd = f"aws s3 sync {remote_path!r} {dest}/ --region {region_str!r}"
    else:
        cmd = f"aws s3 cp {remote_path!r} {dest} --region {region_str!r}"
    _run_ssm_command_and_wait(
        instance_id, cmd, region=region, profile=profile
    )


def _copy_s3_to_workstation(
    bucket: str,
    key: str,
    workstation_name: str,
    remote_path: str,
    *,
    recursive: bool,
    user: str,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Copy from S3 to workstation via SSM (aws s3 cp/sync on instance)."""
    instance_id = resolve_workstation(workstation_name, region=region, profile=profile)
    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
    region_str = region or "us-east-1"
    src = f"s3://{bucket}/{key.rstrip('/')}"
    if recursive:
        cmd = f"aws s3 sync {src}/ {remote_path!r} --region {region_str!r}"
    else:
        cmd = f"aws s3 cp {src} {remote_path!r} --region {region_str!r}"
    _run_ssm_command_and_wait(
        instance_id, cmd, region=region, profile=profile
    )


@click.command("copy")
@click.argument("source")
@click.argument("destination")
@click.option(
    "--recursive",
    "-r",
    is_flag=True,
    default=False,
    help="Recursively copy directories / S3 prefixes.",
)
@click.option(
    "--user",
    "-u",
    default="ubuntu",
    show_default=True,
    help="SSH username on workstations.",
)
@click.option(
    "--identity",
    "-i",
    "identity_file",
    default=None,
    help="Path to SSH private key for workstation copy.",
)
@click.option(
    "--region",
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
    help="Wait for SSM when copying to/from a workstation.",
)
@click.option(
    "--wait-timeout",
    default=300,
    show_default=True,
    help="Seconds to wait for SSM.",
)
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def copy_cmd(
    source: str,
    destination: str,
    recursive: bool,
    user: str,
    identity_file: str | None,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
    stack: str,
) -> None:
    """Copy files between local, workstation, and S3.

    Locations:
      Local:        ./file, /tmp/dir, relative/path
      Workstation:  name:path (e.g. main:/tmp/file, main:~/dir)
      S3:           s3:/key or s3:/path/to/key (desk-managed bucket; no bucket name needed)

    Copy between two local paths or two workstations is not supported.

    \b
    Examples:
      desk copy ./file.txt main:/tmp/file.txt
      desk copy main:/tmp/out.txt ./out.txt
      desk copy ./data s3:/backup/data
      desk copy s3:/backup/data ./restored
      desk copy main:/var/log/app s3:/logs/app -r
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    try:
        src_loc = parse_location(source)
        dest_loc = parse_location(destination)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    _reject_local_to_local(src_loc, dest_loc)
    _reject_workstation_to_workstation(src_loc, dest_loc)

    key_path = None
    if src_loc.kind == LocationKind.WORKSTATION or dest_loc.kind == LocationKind.WORKSTATION:
        key_path = identity_file or get_default_private_key_path()
        if not key_path:
            raise click.ClickException(
                "No SSH key found. Create ~/.ssh/id_ed25519 (or id_rsa) or use -i PATH."
            )
        if not os.path.exists(key_path):
            raise click.ClickException(f"Key not found at {key_path}.")

    bucket = None
    if src_loc.kind == LocationKind.S3 or dest_loc.kind == LocationKind.S3:
        try:
            bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e

    try:
        if src_loc.kind == LocationKind.LOCAL and dest_loc.kind == LocationKind.WORKSTATION:
            _copy_local_to_workstation(
                src_loc.path,
                dest_loc.workstation_name or "",
                dest_loc.path,
                recursive=recursive,
                user=user,
                key_path=key_path or "",
                region=region,
                profile=profile,
                wait=wait,
                wait_timeout=wait_timeout,
            )
        elif src_loc.kind == LocationKind.WORKSTATION and dest_loc.kind == LocationKind.LOCAL:
            _copy_workstation_to_local(
                src_loc.workstation_name or "",
                src_loc.path,
                dest_loc.path,
                recursive=recursive,
                user=user,
                key_path=key_path or "",
                region=region,
                profile=profile,
                wait=wait,
                wait_timeout=wait_timeout,
            )
        elif src_loc.kind == LocationKind.LOCAL and dest_loc.kind == LocationKind.S3:
            _copy_local_to_s3(
                src_loc.path,
                bucket or "",
                dest_loc.path,
                recursive=recursive,
                region=region,
                profile=profile,
            )
        elif src_loc.kind == LocationKind.S3 and dest_loc.kind == LocationKind.LOCAL:
            _copy_s3_to_local(
                bucket or "",
                src_loc.path,
                dest_loc.path,
                recursive=recursive,
                region=region,
                profile=profile,
            )
        elif src_loc.kind == LocationKind.WORKSTATION and dest_loc.kind == LocationKind.S3:
            _copy_workstation_to_s3(
                src_loc.workstation_name or "",
                src_loc.path,
                bucket or "",
                dest_loc.path,
                recursive=recursive,
                user=user,
                region=region,
                profile=profile,
                wait=wait,
                wait_timeout=wait_timeout,
            )
        elif src_loc.kind == LocationKind.S3 and dest_loc.kind == LocationKind.WORKSTATION:
            _copy_s3_to_workstation(
                bucket or "",
                src_loc.path,
                dest_loc.workstation_name or "",
                dest_loc.path,
                recursive=recursive,
                user=user,
                region=region,
                profile=profile,
                wait=wait,
                wait_timeout=wait_timeout,
            )
        else:
            raise click.ClickException("Unsupported source/destination combination.")
    except click.ClickException:
        raise
    except Exception as e:
        log.debug("copy failed: %s", e)
        raise click.ClickException(str(e)) from e
