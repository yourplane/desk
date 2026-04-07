"""Shell scripts and tarballs for async recipe steps (SSM)."""

from __future__ import annotations

import hashlib
import os
import shlex
import tarfile
import tempfile
from typing import Any

import click

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import AMI_BUILD_COMMENT_PREFIX
from desk_cli.ami_build.build_config import normalize_build_id_arg


def ami_build_comment_tag(build_id: str, step_index: int, kind: str) -> str:
    """SSM Comment value correlating an invocation to a recipe step (AWS max 100 chars)."""
    bid = normalize_build_id_arg(build_id)
    base = f"{AMI_BUILD_COMMENT_PREFIX}{bid}:{step_index}:{kind}"
    if len(base) <= 100:
        return base
    short = hashlib.sha256(bid.encode()).hexdigest()[:12]
    return f"{AMI_BUILD_COMMENT_PREFIX}{short}:{step_index}:{kind}"


def parse_ami_build_comment(comment: str | None, build_id: str) -> tuple[int, str] | None:
    if not comment or not comment.startswith(AMI_BUILD_COMMENT_PREFIX):
        return None
    bid = normalize_build_id_arg(build_id)
    rest = comment[len(AMI_BUILD_COMMENT_PREFIX) :]
    parts = rest.split(":")
    if len(parts) < 3:
        return None
    try:
        step_index = int(parts[-2])
        kind = parts[-1]
    except (ValueError, IndexError):
        return None
    id_part = ":".join(parts[:-2])
    if id_part == bid:
        return (step_index, kind)
    if id_part == hashlib.sha256(bid.encode()).hexdigest()[:12]:
        return (step_index, kind)
    return None


def normalize_shell_for_compare(cmd: str) -> str:
    return " ".join(cmd.split())


def staged_s3_object_key(src: str) -> str:
    s = src.strip()
    if not s.startswith("s3:/"):
        raise click.ClickException(
            f"Staged copy source must be s3:/… (got {src!r}). Re-run `desk ami build create`."
        )
    return s[4:].lstrip("/")


def tar_member_name_for_single_file(source: str, dest: str) -> str:
    """Path inside the tarball for a single-file copy (matches final basename on extract)."""
    if dest.endswith("/") or dest.endswith(os.sep):
        return os.path.basename(source)
    d = dest.rstrip("/")
    base = os.path.basename(d)
    if not base:
        return os.path.basename(source)
    return base


def parent_dir_for_file_copy_dest(dest: str) -> str:
    """Directory to pass to ``tar -C`` for a single-file copy (handles ``…/`` targets)."""
    if dest.endswith("/") or dest.endswith(os.sep):
        return dest.rstrip("/") or "."
    d = os.path.dirname(dest)
    return d if d else "."


def write_ami_copy_tarball(
    resolved: str,
    dest: str,
    *,
    recursive: bool,
) -> str:
    """Create a temporary tar file with full permission bits preserved. Caller must unlink."""
    fd, tmp_path = tempfile.mkstemp(suffix=".tar")
    os.close(fd)
    try:
        with tarfile.open(tmp_path, "w", format=tarfile.GNU_FORMAT) as tf:
            if os.path.isdir(resolved):
                assert recursive
                tf.add(os.path.abspath(resolved), arcname=".", recursive=True)
            else:
                arc = tar_member_name_for_single_file(resolved, dest)
                tf.add(os.path.abspath(resolved), arcname=arc, recursive=False)
        return tmp_path
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def async_shell_for_copy_step(
    copy_item: dict[str, Any],
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    """Download a staged tar bundle via ``curl`` and extract with ``tar`` (preserves modes)."""
    src = copy_item["source"]
    raw_dest = copy_item["dest"]
    recursive = copy_item.get("recursive", False)
    key = staged_s3_object_key(src)
    url = aws_shim.generate_presigned_get_object_url(
        bucket, key, region=region, profile=profile
    )
    lines = [
        "set -eu",
        "TMP=$(mktemp)",
        "trap 'rm -f \"$TMP\"' EXIT",
        f"curl -fsSL {shlex.quote(url)} -o \"$TMP\"",
    ]
    if recursive:
        dest = raw_dest.rstrip("/")
        lines.append(f"install -d -m 0755 {shlex.quote(dest)}")
        lines.append(f'tar -xf "$TMP" -C {shlex.quote(dest)}')
    else:
        dest_dir = parent_dir_for_file_copy_dest(raw_dest)
        lines.append(f"install -d -m 0755 {shlex.quote(dest_dir)}")
        lines.append(f'tar -xf "$TMP" -C {shlex.quote(dest_dir)}')
    return "\n".join(lines) + "\n"


def async_shell_for_run_step(
    run_value: str,
    step_index: int,
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    rv = run_value.strip()
    if rv.startswith("s3:/"):
        key = staged_s3_object_key(rv)
        tmp = f"/tmp/desk-ami-run-{step_index}.sh"
        url = aws_shim.generate_presigned_get_object_url(
            bucket, key, region=region, profile=profile
        )
        return (
            "set -eu\n"
            f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(tmp)}\n"
            f"bash {shlex.quote(tmp)}\n"
        )
    return rv


def expected_async_shell_for_step(
    step: dict[str, Any],
    step_index: int,
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    if "run" in step:
        return async_shell_for_run_step(
            step["run"], step_index, bucket=bucket, region=region, profile=profile
        )
    return async_shell_for_copy_step(
        step["copy"], bucket=bucket, region=region, profile=profile
    )
