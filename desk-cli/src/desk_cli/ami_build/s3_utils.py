"""S3 helpers for staged AMI builds."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

import click
from botocore.exceptions import ClientError

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import AMI_RESULT_KEY


def read_s3_object_json(
    s3: Any,
    bucket: str,
    key: str,
) -> dict[str, Any] | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise
    raw = json.loads(obj["Body"].read())
    if not isinstance(raw, dict):
        raise click.ClickException(f"S3 object {key} must be a JSON object.")
    return raw


def put_s3_object_json(
    s3: Any,
    bucket: str,
    key: str,
    payload: dict[str, Any],
) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def merge_ami_result_s3(
    s3: Any,
    bucket: str,
    prefix: str,
    updates: dict[str, Any],
) -> None:
    key = f"{prefix}{AMI_RESULT_KEY}"
    existing = read_s3_object_json(s3, bucket, key) or {}
    merged = {**existing, **updates}
    put_s3_object_json(s3, bucket, key, merged)


def new_build_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{secrets.token_hex(4)}"


def move_s3_prefix_within_bucket(
    bucket: str,
    src_prefix: str,
    dest_prefix: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Copy all objects under src_prefix to dest_prefix and delete sources."""
    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    pages = list(paginator.paginate(Bucket=bucket, Prefix=src_prefix))
    keys: list[str] = []
    for page in pages:
        for obj in page.get("Contents") or []:
            keys.append(obj["Key"])
    if not keys:
        raise click.ClickException(
            f"No objects found under s3://{bucket}/{src_prefix}"
        )
    for key in keys:
        suffix = key[len(src_prefix) :]
        new_key = f"{dest_prefix}{suffix}"
        s3.copy_object(
            Bucket=bucket,
            Key=new_key,
            CopySource={"Bucket": bucket, "Key": key},
        )
        s3.delete_object(Bucket=bucket, Key=key)
