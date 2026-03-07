"""desk copy - Copy files between local, S3, and desk workstations via SSM."""

from __future__ import annotations

import os
import time

import click

from desk.aws import (
    get_command_invocation,
    is_ssm_ready,
    resolve_workstation,
    run_ssm_s3_copy,
    send_ssm_command,
    wait_for_ssm_command,
    wait_for_ssm_ready,
)
from desk.config import get_default_profile, get_default_region


def _is_s3_uri(path: str) -> bool:
    return path.startswith("s3://")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Return (bucket, key) for s3://bucket/key."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an S3 URI: {uri}")
    rest = uri[5:].strip("/")
    if "/" not in rest:
        return rest, ""
    bucket, key = rest.split("/", 1)
    return bucket, key


def _is_workstation_path(path: str) -> bool:
    """True if path is workstation_name:remote_path or :remote_path."""
    if path.startswith(":"):
        return True
    return ":" in path and not path.startswith("s3:")


def _parse_workstation_path(path: str) -> tuple[str, str]:
    """Return (workstation_name, remote_path). For :path return ("", path)."""
    if path.startswith(":"):
        return "", path[1:]
    if ":" in path:
        name, rest = path.split(":", 1)
        return name.strip(), rest
    return "", path


def _copy_local_to_s3(
    local_path: str,
    bucket: str,
    key: str,
    region: str | None,
    profile: str | None,
    recursive: bool,
) -> None:
    import boto3

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    if os.path.isfile(local_path):
        s3.upload_file(local_path, bucket, key)
    elif recursive and os.path.isdir(local_path):
        for root, _dirs, files in os.walk(local_path):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_path)
                k = f"{key.rstrip('/')}/{rel}".replace("\\", "/")
                s3.upload_file(full, bucket, k)
    else:
        raise click.ClickException(
            f"Local path {local_path} is not a file; use -r for directories."
        )


def _copy_s3_to_local(
    bucket: str,
    key: str,
    local_path: str,
    region: str | None,
    profile: str | None,
    recursive: bool,
) -> None:
    import boto3

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    if recursive:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=key.rstrip("/") + "/"):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                rel = k[len(key) :].lstrip("/")
                local_file = os.path.join(local_path, rel)
                os.makedirs(os.path.dirname(local_file), exist_ok=True)
                s3.download_file(bucket, k, local_file)
    else:
        if not key or key.endswith("/"):
            raise click.ClickException("S3 key must be a file when not using -r")
        if os.path.isdir(local_path) or local_path.endswith(os.sep):
            local_path = os.path.join(local_path, os.path.basename(key))
        s3.download_file(bucket, key, local_path)


def _copy_s3_to_workstation(
    bucket: str,
    key: str,
    instance_id: str,
    dest_path: str,
    region: str | None,
    profile: str | None,
    recursive: bool,
) -> None:
    s3_uri = f"s3://{bucket}/{key}"
    cmd_id = run_ssm_s3_copy(
        instance_id,
        s3_uri,
        dest_path,
        recursive=recursive,
        region=region,
        profile=profile,
    )
    result = wait_for_ssm_command(
        cmd_id, instance_id, region=region, profile=profile, timeout=900
    )
    if result.status != "Success" or (result.exit_code is not None and result.exit_code != 0):
        raise click.ClickException(
            f"Copy failed: {result.stderr or result.stdout or result.status}"
        )


def _copy_workstation_to_s3(
    instance_id: str,
    source_path: str,
    bucket: str,
    key: str,
    region: str | None,
    profile: str | None,
    recursive: bool,
) -> None:
    s3_uri = f"s3://{bucket}/{key}"
    rec = "--recursive" if recursive else ""
    cmd = f"aws s3 cp {source_path} {s3_uri} {rec}".strip()
    cmd_id = send_ssm_command(
        instance_id, cmd, region=region, profile=profile, timeout_seconds=600
    )
    result = wait_for_ssm_command(
        cmd_id, instance_id, region=region, profile=profile, timeout=900
    )
    if result.status != "Success" or (result.exit_code is not None and result.exit_code != 0):
        raise click.ClickException(
            f"Copy failed: {result.stderr or result.stdout or result.status}"
        )


@click.command("copy")
@click.argument("source")
@click.argument("destination")
@click.option(
    "--recursive",
    "-r",
    is_flag=True,
    help="Recursively copy directories.",
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
    "--artifact-bucket",
    default=None,
    envvar="DESK_ARTIFACT_BUCKET",
    help="S3 bucket for staging when copying between local and workstation.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for instance to be SSM-ready when workstation is involved.",
)
def copy_cmd(
    source: str,
    destination: str,
    recursive: bool,
    region: str | None,
    profile: str | None,
    artifact_bucket: str | None,
    wait: bool,
) -> None:
    """Copy files between local paths, S3 URIs, and desk workstations.

    SOURCE and DESTINATION can be:
      - Local path: /path or ./path
      - S3: s3://bucket/key
      - Workstation: workstation_name:/remote/path or :/remote/path (default workstation)

    When one side is local and the other is a workstation, copies are staged via S3;
    set DESK_ARTIFACT_BUCKET or --artifact-bucket.

    \b
    Examples:
      desk copy ./file.txt s3://my-bucket/file.txt
      desk copy s3://my-bucket/file.txt ./file.txt
      desk copy main:/tmp/out ./out --artifact-bucket my-bucket
      desk copy ./dist main:/opt/app -r --artifact-bucket my-bucket
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

    src_s3 = _is_s3_uri(source)
    dest_s3 = _is_s3_uri(destination)
    src_ws = _is_workstation_path(source)
    dest_ws = _is_workstation_path(destination)

    if src_ws and dest_ws:
        raise click.ClickException(
            "Copy between two workstations is not supported; use S3 as intermediate."
        )
    if src_s3 and dest_s3:
        raise click.ClickException(
            "Copy between two S3 locations: use aws s3 cp."
        )

    # Resolve workstation if needed
    instance_id: str | None = None
    if src_ws:
        ws_name, _ = _parse_workstation_path(source)
        if not ws_name:
            raise click.ClickException(
                "Workstation name required when source is remote (e.g. main:/path)."
            )
        instance_id = resolve_workstation(ws_name, region=region, profile=profile)
    if dest_ws:
        ws_name, _ = _parse_workstation_path(destination)
        if not ws_name:
            raise click.ClickException(
                "Workstation name required when destination is remote (e.g. main:/path)."
            )
        instance_id = resolve_workstation(ws_name, region=region, profile=profile)

    if instance_id and wait:
        if not is_ssm_ready(instance_id, region=region, profile=profile):
            if not wait_for_ssm_ready(
                instance_id, region=region, profile=profile, timeout=300
            ):
                raise click.ClickException(
                    f"Instance {instance_id} did not become SSM-ready."
                )

    # Local ↔ S3
    if not src_ws and not dest_ws:
        if src_s3:
            bucket, key = _parse_s3_uri(source)
            _copy_s3_to_local(
                bucket, key, destination, region, profile, recursive
            )
        else:
            bucket, key = _parse_s3_uri(destination)
            _copy_local_to_s3(
                source, bucket, key, region, profile, recursive
            )
        click.echo("Done.")
        return

    # One side is workstation; need staging bucket for local
    if (src_ws or dest_ws) and (not src_s3 and not dest_s3):
        if not artifact_bucket:
            raise click.ClickException(
                "Copy between local and workstation requires --artifact-bucket or DESK_ARTIFACT_BUCKET."
            )
        # Staging key: desk-copy/<random>/file
        import secrets
        stage_prefix = f"desk-copy/{secrets.token_hex(8)}"
        if dest_ws:
            # local → workstation: upload to S3, then SSM s3 cp to instance
            _, dest_path = _parse_workstation_path(destination)
            if os.path.isfile(source):
                key = f"{stage_prefix}/{os.path.basename(source)}"
                _copy_local_to_s3(
                    source, artifact_bucket, key, region, profile, False
                )
                _copy_s3_to_workstation(
                    artifact_bucket,
                    key,
                    instance_id,
                    dest_path,
                    region,
                    profile,
                    False,
                )
            else:
                prefix = f"{stage_prefix}/"
                _copy_local_to_s3(
                    source, artifact_bucket, prefix, region, profile, True
                )
                _copy_s3_to_workstation(
                    artifact_bucket,
                    prefix,
                    instance_id,
                    dest_path,
                    region,
                    profile,
                    True,
                )
        else:
            # workstation → local: SSM s3 cp from instance to S3, then download
            _, src_path = _parse_workstation_path(source)
            _copy_workstation_to_s3(
                instance_id,
                src_path,
                artifact_bucket,
                stage_prefix + "/",
                region,
                profile,
                recursive,
            )
            _copy_s3_to_local(
                artifact_bucket,
                stage_prefix + "/",
                destination,
                region,
                profile,
                True,
            )
        click.echo("Done.")
        return

    # S3 ↔ workstation
    if src_s3:
        bucket, key = _parse_s3_uri(source)
        _, dest_path = _parse_workstation_path(destination)
        _copy_s3_to_workstation(
            bucket, key, instance_id, dest_path, region, profile, recursive
        )
    else:
        bucket, key = _parse_s3_uri(destination)
        _, src_path = _parse_workstation_path(source)
        _copy_workstation_to_s3(
            instance_id, src_path, bucket, key, region, profile, recursive
        )
    click.echo("Done.")
