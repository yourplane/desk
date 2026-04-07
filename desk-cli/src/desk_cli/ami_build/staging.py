"""Upload AMI build recipes and artifacts to S3."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import click

from desk.config import get_desk_settings

from desk_cli import __version__
from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import AMI_BUILDS_PREFIX, AMI_COPY_BUNDLE_NAME
from desk_cli.ami_build.build_config import (
    artifact_rel_path,
    get_build_steps,
    load_build_config,
    resolve_copy_source,
    resolve_run_for_build,
    s3_uri_for_key,
    validate_build_recipe_config,
)
from desk_cli.ami_build.shell import write_ami_copy_tarball
from desk_cli.ami_build.s3_utils import new_build_id


def stage_ami_build_to_s3(
    config_file: str,
    *,
    stack: str,
) -> tuple[str, str, str]:
    """Upload recipe and artifacts; returns ``(build_id, bucket, s3_key_prefix)``."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    config = load_build_config(config_file)
    validate_build_recipe_config(config, config_file)
    steps = get_build_steps(config)
    ami_name = config.get("ami_name")
    assert ami_name
    config_dir = os.path.dirname(os.path.abspath(config_file))

    try:
        bucket = aws_shim.get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    build_id = new_build_id()
    prefix = f"{AMI_BUILDS_PREFIX}{build_id}/"

    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    normalized_steps: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        if "run" in step:
            cmd = step["run"]
            resolved, is_file = resolve_run_for_build(cmd, config_dir)
            if is_file:
                rel = artifact_rel_path(resolved, config_dir)
                key = f"{prefix}files/run/{i}/{rel}"
                s3.upload_file(resolved, bucket, key)
                normalized_steps.append({"run": s3_uri_for_key(key)})
            else:
                normalized_steps.append({"run": cmd})
        else:
            item = dict(step["copy"])
            src = item["source"]
            recursive = item.get("recursive", False)
            resolved = resolve_copy_source(src, config_dir)
            if not os.path.exists(resolved):
                raise click.ClickException(
                    f"Copy step {i}: source path does not exist: {resolved}"
                )
            if os.path.isdir(resolved):
                if not recursive:
                    raise click.ClickException(
                        f"Copy step {i}: source is a directory; set \"recursive\": true."
                    )
            tar_path = write_ami_copy_tarball(
                resolved, item["dest"], recursive=recursive
            )
            try:
                key = f"{prefix}files/copy/{i}/{AMI_COPY_BUNDLE_NAME}"
                s3.upload_file(tar_path, bucket, key)
            finally:
                os.unlink(tar_path)
            item["source"] = s3_uri_for_key(key)
            normalized_steps.append({"copy": item})

    out_config: dict[str, Any] = {
        "ami_name": ami_name,
        "instance_type": config.get("instance_type", "t3.medium"),
        "steps": normalized_steps,
    }

    config_key = f"{prefix}config.json"
    manifest_key = f"{prefix}manifest.json"
    manifest = {
        "build_id": build_id,
        "ami_name": ami_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "original_config_path": os.path.abspath(config_file),
        "desk_version": __version__,
    }

    s3.put_object(
        Bucket=bucket,
        Key=config_key,
        Body=json.dumps(out_config, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
    )

    return build_id, bucket, prefix
