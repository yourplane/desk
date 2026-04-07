"""List staged or archived async AMI builds in S3."""

from __future__ import annotations

import json
from typing import Any

import click

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX


def list_staged_builds(*, archived: bool, output: str, stack: str) -> None:
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        bucket = aws_shim.get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    base = AMI_BUILD_ARCHIVE_PREFIX if archived else AMI_BUILDS_PREFIX
    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    resp = s3.list_objects_v2(Bucket=bucket, Prefix=base, Delimiter="/")
    prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes") or []]
    builds: list[tuple[str, dict[str, Any] | None]] = []
    for p in sorted(prefixes):
        bid = p[len(base) :].rstrip("/")
        if not bid:
            continue
        manifest_key = f"{p}manifest.json"
        man: dict[str, Any] | None = None
        try:
            obj = s3.get_object(Bucket=bucket, Key=manifest_key)
            man = json.loads(obj["Body"].read())
        except Exception:
            pass
        builds.append((bid, man))

    if not builds:
        click.echo("No AMI builds found.")
        return

    if output == "plain":
        for bid, man in builds:
            name = (man or {}).get("ami_name", "-")
            created = (man or {}).get("created_at", "-")
            click.echo(f"{bid}\t{name}\t{created}")
        return

    max_id = max(len(b[0]) for b in builds)
    max_name = max(len((b[1] or {}).get("ami_name") or "-") for b in builds)
    max_created = max(len((b[1] or {}).get("created_at") or "-") for b in builds)
    max_id = max(max_id, len("BUILD ID"))
    max_name = max(max_name, len("AMI NAME"))
    max_created = max(max_created, len("CREATED (UTC)"))

    header = (
        f"{'BUILD ID':<{max_id}}  {'AMI NAME':<{max_name}}  {'CREATED (UTC)':<{max_created}}"
    )
    click.echo(header)
    click.echo("-" * len(header))
    for bid, man in builds:
        name = (man or {}).get("ami_name", "-")
        created = (man or {}).get("created_at", "-")
        click.echo(f"{bid:<{max_id}}  {name:<{max_name}}  {created:<{max_created}}")
