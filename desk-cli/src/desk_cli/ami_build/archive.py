"""Archive staged AMI build prefixes in S3."""

from __future__ import annotations

import click

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX
from desk_cli.ami_build.build_config import normalize_build_id_arg
from desk_cli.ami_build.s3_utils import move_s3_prefix_within_bucket


def archive_staged_ami_build_prefix(build_id: str, *, stack: str) -> None:
    """Move ami-builds/<id>/ to ami-build-archive/<id>/ (same layout as ``desk ami build cancel``)."""
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
    src_prefix = f"{AMI_BUILDS_PREFIX}{bid}/"
    dest_prefix = f"{AMI_BUILD_ARCHIVE_PREFIX}{bid}/"
    move_s3_prefix_within_bucket(
        bucket, src_prefix, dest_prefix, region=region, profile=profile
    )
    click.echo(f"Archived AMI build {bid} to s3:/{dest_prefix}")
