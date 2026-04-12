"""desk copy - Copy files between local, workstation, and S3 using SSM SendCommand (no sessions)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import click

from desk.aws import (
    get_command_invocation,
    get_desk_copy_bucket,
    is_ssm_ready,
    resolve_workstation,
    send_ssm_command,
    wait_for_ssm_ready,
)
from desk.config import get_desk_settings
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


def _reject_copy_without_s3(src: Location, dest: Location) -> None:
    """Reject copy when neither source nor destination is S3 (no local<->workstation)."""
    if src.kind != LocationKind.S3 and dest.kind != LocationKind.S3:
        raise click.ClickException(
            "At least one of source or destination must be S3 (s3:/key). "
            "Copy between local and workstation is not supported."
        )


def _copy_local_s3(
    local_path: str,
    bucket: str,
    key: str,
    *,
    to_s3: bool,
    recursive: bool,
    region: str | None,
    profile: str | None,
) -> None:
    """Copy between local and desk S3 bucket (direction from to_s3)."""
    import boto3

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    if to_s3:
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
    else:
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


def _copy_workstation_s3(
    workstation_name: str,
    remote_path: str,
    bucket: str,
    key: str,
    *,
    to_s3: bool,
    recursive: bool,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Copy between workstation and S3 via SSM (direction from to_s3)."""
    instance_id = resolve_workstation(workstation_name, region=region, profile=profile)
    if wait and not is_ssm_ready(instance_id, region=region, profile=profile):
        if not wait_for_ssm_ready(
            instance_id, region=region, profile=profile, timeout=wait_timeout
        ):
            raise click.ClickException(
                f"Instance {instance_id} did not become SSM-ready within {wait_timeout}s."
            )
    region_str = region or "us-east-1"
    s3_uri = f"s3://{bucket}/{key.rstrip('/')}"
    if to_s3:
        if recursive:
            cmd = f"aws s3 sync {remote_path!r} {s3_uri}/ --region {region_str!r}"
        else:
            cmd = f"aws s3 cp {remote_path!r} {s3_uri} --region {region_str!r}"
    else:
        if recursive:
            cmd = f"aws s3 sync {s3_uri}/ {remote_path!r} --region {region_str!r}"
        else:
            cmd = f"aws s3 cp {s3_uri} {remote_path!r} --region {region_str!r}"
    _run_ssm_command_and_wait(
        instance_id, cmd, region=region, profile=profile
    )


def shell_command_s3_to_workstation(
    bucket: str,
    key: str,
    remote_path: str,
    *,
    recursive: bool,
    region: str | None,
) -> str:
    """Shell line(s) for ``aws s3 cp``/``sync`` from desk bucket key to a path on the instance.

    Matches :func:`_copy_workstation_s3` (S3 → workstation) without resolving the workstation
    or waiting for the command — used by async AMI build to send a single SSM command.
    """
    region_str = region or "us-east-1"
    s3_uri = f"s3://{bucket}/{key.rstrip('/')}"
    if recursive:
        return f"aws s3 sync {s3_uri}/ {remote_path!r} --region {region_str!r}"
    return f"aws s3 cp {s3_uri} {remote_path!r} --region {region_str!r}"


def _dispatch_copy(
    src_loc: Location,
    dest_loc: Location,
    *,
    bucket: str,
    recursive: bool,
    region: str | None,
    profile: str | None,
    wait: bool,
    wait_timeout: int,
) -> None:
    """Dispatch to the right copy implementation by (source_kind, dest_kind)."""

    def run_local_s3(to_s3: bool) -> None:
        _copy_local_s3(
            src_loc.path if to_s3 else dest_loc.path,
            bucket or "",
            dest_loc.path if to_s3 else src_loc.path,
            to_s3=to_s3,
            recursive=recursive,
            region=region,
            profile=profile,
        )

    def run_workstation_s3(to_s3: bool) -> None:
        _copy_workstation_s3(
            (src_loc.workstation_name if to_s3 else dest_loc.workstation_name) or "",
            src_loc.path if to_s3 else dest_loc.path,
            bucket or "",
            dest_loc.path if to_s3 else src_loc.path,
            to_s3=to_s3,
            recursive=recursive,
            region=region,
            profile=profile,
            wait=wait,
            wait_timeout=wait_timeout,
        )

    _HANDLERS: dict[tuple[LocationKind, LocationKind], Callable[[], None]] = {
        (LocationKind.LOCAL, LocationKind.S3): lambda: run_local_s3(True),
        (LocationKind.S3, LocationKind.LOCAL): lambda: run_local_s3(False),
        (LocationKind.WORKSTATION, LocationKind.S3): lambda: run_workstation_s3(True),
        (LocationKind.S3, LocationKind.WORKSTATION): lambda: run_workstation_s3(False),
    }
    key = (src_loc.kind, dest_loc.kind)
    handler = _HANDLERS.get(key)
    if not handler:
        raise click.ClickException("Unsupported source/destination combination.")
    handler()


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
    wait: bool,
    wait_timeout: int,
    stack: str,
) -> None:
    """Copy files between local, workstation, and S3 (one end must be S3).

    Locations:
      Local:        ./file, /tmp/dir, relative/path
      Workstation:  name:path (e.g. main:/tmp/file, main:~/dir)
      S3:           s3:/key or s3:/path/to/key (desk-managed bucket; no bucket name needed)

    At least one of source or destination must be S3. Copy between only local and
    workstation is not supported.

    \b
    Examples:
      desk copy ./data s3:/backup/data
      desk copy s3:/backup/data ./restored
      desk copy main:/var/log/app s3:/logs/app -r
      desk copy s3:/logs/app main:/tmp/restored -r
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        src_loc = parse_location(source)
        dest_loc = parse_location(destination)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    _reject_local_to_local(src_loc, dest_loc)
    _reject_workstation_to_workstation(src_loc, dest_loc)
    _reject_copy_without_s3(src_loc, dest_loc)

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    try:
        _dispatch_copy(
            src_loc,
            dest_loc,
            bucket=bucket,
            recursive=recursive,
            region=region,
            profile=profile,
            wait=wait,
            wait_timeout=wait_timeout,
        )
    except click.ClickException:
        raise
    except Exception as e:
        log.debug("copy failed: %s", e)
        raise click.ClickException(str(e)) from e
