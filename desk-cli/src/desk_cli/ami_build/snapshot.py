"""Resolve async AMI build state from S3, EC2, and AMI APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click
from botocore.exceptions import ClientError

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import (
    AMI_BUILDS_PREFIX,
    AMI_RESULT_KEY,
    BUILDER_INSTANCE_KEY,
)
from desk_cli.ami_build.build_config import normalize_build_id_arg
from desk_cli.ami_build.s3_utils import read_s3_object_json


@dataclass(frozen=True)
class AsyncAmiBuildSnapshot:
    """AWS-derived state for `desk ami build status` / `step` (no extra local state)."""

    build_id: str
    bucket: str
    s3_prefix: str
    config: dict[str, Any]
    recorded_instance_id: str | None
    ec2_state: str | None
    ec2_missing: bool
    ssm_ready: bool | None
    ami_result: dict[str, Any] | None
    registered_ami_id: str | None
    registered_ami_state: str | None
    async_pipeline_fully_complete: bool


def safe_get_instance_state(
    instance_id: str,
    *,
    region: str | None,
    profile: str | None,
) -> str | None:
    try:
        return aws_shim.get_instance_state(instance_id, region=region, profile=profile)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidInstanceID.NotFound":
            return None
        raise


def async_ami_image_id_from_result(ami_result: dict[str, Any] | None) -> str | None:
    if not ami_result:
        return None
    raw = ami_result.get("image_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_async_ami_build_snapshot(build_id: str, *, stack: str) -> AsyncAmiBuildSnapshot:
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    bid = normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")

    try:
        bucket = aws_shim.get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    prefix = f"{AMI_BUILDS_PREFIX}{bid}/"
    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    cfg_key = f"{prefix}config.json"
    config = read_s3_object_json(s3, bucket, cfg_key)
    if config is None:
        raise click.ClickException(
            f"No staged AMI build found for id {bid!r} "
            f"(missing {AMI_BUILDS_PREFIX}{bid}/config.json)."
        )

    builder_key = f"{prefix}{BUILDER_INSTANCE_KEY}"
    builder_doc = read_s3_object_json(s3, bucket, builder_key)
    recorded_id: str | None = None
    if builder_doc is not None:
        iid = builder_doc.get("instance_id")
        if iid is None:
            recorded_id = None
        elif not isinstance(iid, str):
            raise click.ClickException(
                f"{BUILDER_INSTANCE_KEY} must contain a string 'instance_id'."
            )
        elif not iid.strip():
            raise click.ClickException(
                f"{BUILDER_INSTANCE_KEY} 'instance_id' must be non-empty."
            )
        else:
            recorded_id = iid.strip()

    ec2_missing = False
    ec2_state: str | None = None
    ssm_ready: bool | None = None

    if recorded_id:
        ec2_state = safe_get_instance_state(recorded_id, region=region, profile=profile)
        if ec2_state is None:
            ec2_missing = True
        elif ec2_state in ("running", "pending"):
            ssm_ready = aws_shim.is_ssm_ready(recorded_id, region=region, profile=profile)
        elif ec2_state in ("stopped", "stopping", "shutting-down"):
            ssm_ready = False
        elif ec2_state == "terminated":
            ssm_ready = None

    result_key = f"{prefix}{AMI_RESULT_KEY}"
    ami_result = read_s3_object_json(s3, bucket, result_key)

    reg_image_id = async_ami_image_id_from_result(ami_result)
    reg_ami_state: str | None = None
    if reg_image_id:
        reg_ami_state = aws_shim.get_ami_state(reg_image_id, region=region, profile=profile)

    pipeline_done = False
    if ami_result and ami_result.get("pipeline_complete") is True and reg_image_id:
        pipeline_done = True
    elif (
        reg_image_id
        and reg_ami_state == "available"
        and (ec2_state == "terminated" or ec2_missing)
    ):
        pipeline_done = True

    return AsyncAmiBuildSnapshot(
        build_id=bid,
        bucket=bucket,
        s3_prefix=prefix,
        config=config,
        recorded_instance_id=recorded_id,
        ec2_state=ec2_state,
        ec2_missing=ec2_missing,
        ssm_ready=ssm_ready,
        ami_result=ami_result,
        registered_ami_id=reg_image_id,
        registered_ami_state=reg_ami_state,
        async_pipeline_fully_complete=pipeline_done,
    )
