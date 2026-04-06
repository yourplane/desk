"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
import click
from botocore.exceptions import ClientError

from desk.aws import (
    create_ami,
    create_workstation,
    generate_presigned_get_object_url,
    get_ami_state,
    get_command_invocation,
    get_desk_copy_bucket,
    get_instance_state,
    get_latest_ubuntu_ami,
    get_ssm_command,
    is_ssm_ready,
    list_amis,
    list_command_invocations_for_instance,
    resolve_workstation,
    send_ssm_command,
    terminate_instance,
    wait_for_ami_available,
    wait_for_instance_state,
)
from desk_cli import __version__
from desk.config import get_desk_settings


AMI_BUILDS_PREFIX = "ami-builds/"
AMI_BUILD_ARCHIVE_PREFIX = "ami-build-archive/"
# Written by `desk ami build step` after the builder instance is launched.
BUILDER_INSTANCE_KEY = "builder-instance.json"
# Post-recipe AMI registration progress (image id, completion flag).
AMI_RESULT_KEY = "ami-result.json"
# SSM Run Command Comment prefix to map invocations to recipe steps (≤100 chars; see _ami_build_comment_tag).
AMI_BUILD_COMMENT_PREFIX = "desk-ami-build:"
# Single tar object per copy step under files/copy/<i>/ (async AMI staging).
AMI_COPY_BUNDLE_NAME = "bundle.tar"

_SPINNER_FRAMES = ("|", "/", "-", "\\")

# Compact status labels (step_group)
_GROUP_STAGE = "Stage recipe"
_GROUP_LAUNCH = "Launch instance"
_GROUP_WAIT_SSM = "Wait for SSM"
_GROUP_BUILD = "Build Commands"
_GROUP_REGISTER = "Register AMI"
_GROUP_WAIT_AMI = "Wait for AMI"
_GROUP_TERMINATE = "Terminate builder"


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def _compact_line_ok(group: str, details: str) -> None:
    click.secho(f"✓ {group}  {details}", fg="green")


def _compact_line_fail(group: str, details: str) -> None:
    click.secho(f"✗ {group}  {details}", fg="red")


def _compact_line_pending(group: str, details: str) -> None:
    click.echo(f"○ {group}  {details}")


def _compact_line_working(group: str, details: str) -> None:
    """In-progress snapshot (no animation), e.g. for ``status``."""
    click.echo(f"… {group}  {details}")


def _print_full_invocation_io(
    instance_id: str,
    command_id: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Print full stdout and stderr for an SSM invocation (used on failure)."""
    try:
        inv = get_command_invocation(
            command_id, instance_id, region=region, profile=profile
        )
    except (ClientError, RuntimeError) as e:
        click.echo(f"(Could not load SSM invocation output: {e})")
        return
    click.echo("Standard output:")
    click.echo(inv.stdout if inv.stdout else "(empty)")
    click.echo("Standard error:", err=True)
    click.echo(inv.stderr if inv.stderr else "(empty)", err=True)


@click.group("ami")
def ami_group() -> None:
    """Manage AMIs from desk workstations."""
    pass


def _load_build_config(path: str) -> dict[str, Any]:
    """Load and validate ami build config from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise click.ClickException("Config must be a JSON object.")
    steps = data.get("steps")
    if steps is not None:
        if not isinstance(steps, list):
            raise click.ClickException("Config 'steps' must be a list.")
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise click.ClickException(
                    f"Config 'steps[{i}]' must be an object (with 'run' or 'copy')."
                )
            if "run" in step and "copy" in step:
                raise click.ClickException(
                    f"Config 'steps[{i}]' must have either 'run' or 'copy', not both."
                )
            if "run" in step:
                if not isinstance(step["run"], str):
                    raise click.ClickException(
                        f"Config 'steps[{i}].run' must be a string."
                    )
            elif "copy" in step:
                c = step["copy"]
                if not isinstance(c, dict) or "source" not in c or "dest" not in c:
                    raise click.ClickException(
                        f"Config 'steps[{i}].copy' must be an object with 'source' and 'dest'."
                    )
            else:
                raise click.ClickException(
                    f"Config 'steps[{i}]' must have 'run' or 'copy'."
                )
    else:
        # Legacy: separate copy and run lists
        copy_list = data.get("copy")
        if copy_list is not None and not isinstance(copy_list, list):
            raise click.ClickException("Config 'copy' must be a list.")
        run_list = data.get("run")
        if run_list is not None and not isinstance(run_list, list):
            raise click.ClickException("Config 'run' must be a list.")
        run_before_copy = data.get("run_before_copy")
        if run_before_copy is not None and not isinstance(run_before_copy, list):
            raise click.ClickException("Config 'run_before_copy' must be a list.")
        for i, item in enumerate(copy_list or []):
            if not isinstance(item, dict) or "source" not in item or "dest" not in item:
                raise click.ClickException(
                    f"Config 'copy[{i}]' must be an object with 'source' and 'dest'."
                )
    return data


def _get_build_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized list of steps (each has 'run' or 'copy') from config."""
    steps = config.get("steps")
    if steps is not None:
        return steps
    # Legacy: run_before_copy, then all copies, then all runs
    out: list[dict[str, Any]] = []
    for cmd in config.get("run_before_copy") or []:
        out.append({"run": cmd})
    for item in config.get("copy") or []:
        out.append({"copy": item})
    for cmd in config.get("run") or []:
        out.append({"run": cmd})
    return out


def _truncate_status_text(s: str, max_len: int = 100) -> str:
    t = " ".join(s.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _describe_recipe_step_for_status(step: dict[str, Any]) -> str:
    """Short human-readable summary of a recipe step for CLI status output."""
    if "run" in step:
        rv = step["run"]
        if not isinstance(rv, str):
            return "run: (invalid config)"
        rv = rv.strip()
        if rv.startswith("s3:/"):
            return f"run script from {_truncate_status_text(rv)}"
        return f"run: {_truncate_status_text(rv)}"
    c = step["copy"]
    src = str(c.get("source", ""))
    dst = str(c.get("dest", ""))
    rec = bool(c.get("recursive", False))
    extra = " (recursive)" if rec else ""
    return (
        f"copy{extra}: {_truncate_status_text(src)} -> {_truncate_status_text(dst)}"
    )


def _registration_ami_name_for_async_build(ami_name: str, build_id: str) -> str:
    """Unique AMI registration name: base ami_name + hyphen + build id (AWS limit 128 chars)."""
    bid = _normalize_build_id_arg(build_id)
    suffix = f"-{bid}"
    base = ami_name.strip()
    max_base = 128 - len(suffix)
    if max_base < 1:
        raise click.ClickException(
            "ami_name is too long to append the build id within AWS's 128-character AMI name limit."
        )
    if len(base) > max_base:
        base = base[:max_base]
    return f"{base}{suffix}"


def _resolve_copy_source(src: str, config_dir: str) -> str:
    if not os.path.isabs(src):
        src = os.path.normpath(os.path.join(config_dir, src))
    return src


def _resolve_run_for_build(cmd: str, config_dir: str) -> tuple[str, bool]:
    """Return (resolved path or original cmd, True if local script file)."""
    if not os.path.isabs(cmd) and ("/" in cmd or cmd.endswith(".sh")):
        candidate = os.path.normpath(os.path.join(config_dir, cmd))
        if os.path.isfile(candidate):
            return candidate, True
    return cmd, False


def _artifact_rel_path(local_path: str, config_dir: str) -> str:
    """Relative path under the staging tree (forward slashes)."""
    config_dir = os.path.abspath(config_dir)
    local_path = os.path.abspath(local_path)
    try:
        rel = os.path.relpath(local_path, config_dir)
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except ValueError:
        pass
    digest = hashlib.sha256(local_path.encode()).hexdigest()[:16]
    base = os.path.basename(local_path.rstrip("/")) or "artifact"
    return f"__outside_config__/{digest}/{base}"


def _s3_uri_for_key(key: str) -> str:
    return f"s3:/{key}"


def _new_build_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{secrets.token_hex(4)}"


def _normalize_build_id_arg(arg: str) -> str:
    s = arg.strip().strip("/")
    for prefix in (AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.rstrip("/")


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
    # Populated in _resolve_async_ami_build_snapshot (one DescribeImages per build when image_id exists).
    registered_ami_id: str | None
    registered_ami_state: str | None
    async_pipeline_fully_complete: bool


def _read_s3_object_json(
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


def _put_s3_object_json(
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


def _merge_ami_result_s3(
    s3: Any,
    bucket: str,
    prefix: str,
    updates: dict[str, Any],
) -> None:
    key = f"{prefix}{AMI_RESULT_KEY}"
    existing = _read_s3_object_json(s3, bucket, key) or {}
    merged = {**existing, **updates}
    _put_s3_object_json(s3, bucket, key, merged)


def _workstation_name_for_staged_build(build_id: str) -> str:
    """Deterministic workstation Name tag for a staged AMI builder instance."""
    bid = _normalize_build_id_arg(build_id)
    base = bid.lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "build"
    base = base[:220]
    return f"ami-build-{base}"


def _move_s3_prefix_within_bucket(
    bucket: str,
    src_prefix: str,
    dest_prefix: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Copy all objects under src_prefix to dest_prefix and delete sources."""
    session = boto3.Session(region_name=region, profile_name=profile)
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


def _archive_staged_ami_build_prefix(build_id: str, *, stack: str) -> None:
    """Move ami-builds/<id>/ to ami-build-archive/<id>/ (same layout as ``desk ami build cancel``)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    bid = _normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")
    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    src_prefix = f"{AMI_BUILDS_PREFIX}{bid}/"
    dest_prefix = f"{AMI_BUILD_ARCHIVE_PREFIX}{bid}/"
    _move_s3_prefix_within_bucket(
        bucket, src_prefix, dest_prefix, region=region, profile=profile
    )
    click.echo(f"Archived AMI build {bid} to s3:/{dest_prefix}")


def _stage_ami_build_to_s3(
    config_file: str,
    *,
    stack: str,
) -> tuple[str, str, str]:
    """Upload recipe and artifacts; returns ``(build_id, bucket, s3_key_prefix)``."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    config = _load_build_config(config_file)
    _validate_build_recipe_config(config, config_file)
    steps = _get_build_steps(config)
    ami_name = config.get("ami_name")
    assert ami_name
    config_dir = os.path.dirname(os.path.abspath(config_file))

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    build_id = _new_build_id()
    prefix = f"{AMI_BUILDS_PREFIX}{build_id}/"

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    normalized_steps: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        if "run" in step:
            cmd = step["run"]
            resolved, is_file = _resolve_run_for_build(cmd, config_dir)
            if is_file:
                rel = _artifact_rel_path(resolved, config_dir)
                key = f"{prefix}files/run/{i}/{rel}"
                s3.upload_file(resolved, bucket, key)
                normalized_steps.append({"run": _s3_uri_for_key(key)})
            else:
                normalized_steps.append({"run": cmd})
        else:
            item = dict(step["copy"])
            src = item["source"]
            recursive = item.get("recursive", False)
            resolved = _resolve_copy_source(src, config_dir)
            if not os.path.exists(resolved):
                raise click.ClickException(
                    f"Copy step {i}: source path does not exist: {resolved}"
                )
            if os.path.isdir(resolved):
                if not recursive:
                    raise click.ClickException(
                        f"Copy step {i}: source is a directory; set \"recursive\": true."
                    )
            tar_path = _write_ami_copy_tarball(
                resolved, item["dest"], recursive=recursive
            )
            try:
                key = f"{prefix}files/copy/{i}/{AMI_COPY_BUNDLE_NAME}"
                s3.upload_file(tar_path, bucket, key)
            finally:
                os.unlink(tar_path)
            item["source"] = _s3_uri_for_key(key)
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


def _print_verbose_recipe_command_io(
    snap: AsyncAmiBuildSnapshot,
    recipe_eval: AsyncRecipeEval,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Print SSM command script and invocation stdout/stderr (for ``status --verbose`` / ``step --verbose``)."""
    if not snap.recorded_instance_id:
        return
    iid = snap.recorded_instance_id
    cid: str | None = None
    if recipe_eval.blocked and recipe_eval.blocked_command_id:
        cid = recipe_eval.blocked_command_id
    elif recipe_eval.in_progress_command_id:
        cid = recipe_eval.in_progress_command_id
    if not cid:
        return
    try:
        doc = get_ssm_command(cid, region=region, profile=profile)
        params = doc.get("Parameters") or {}
        cmds = params.get("commands")
        if isinstance(cmds, list) and cmds:
            click.echo("    Command script:")
            for line in str(cmds[0]).splitlines():
                click.echo(f"      {line}")
        inv = get_command_invocation(cid, iid, region=region, profile=profile)
        click.echo("    StandardOutputContent:")
        click.echo(inv.stdout if inv.stdout else "(empty)")
        click.echo("    StandardErrorContent:")
        click.echo(inv.stderr if inv.stderr else "(empty)", err=True)
    except (ClientError, RuntimeError, OSError) as e:
        click.echo(f"    (Could not load SSM invocation details: {e})")


def _drive_ami_build_run_loop(
    build_id: str,
    *,
    stack: str,
    no_wait: bool,
) -> None:
    """Orchestrate staged create + step loop until pipeline complete, then archive."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    bid = _normalize_build_id_arg(build_id)

    while True:
        snap = _resolve_async_ami_build_snapshot(bid, stack=stack)
        if snap.async_pipeline_fully_complete:
            _archive_staged_ami_build_prefix(bid, stack=stack)
            click.secho("AMI build complete.", fg="green", bold=True)
            return

        recipe_eval = _maybe_evaluate_async_recipe(snap)

        if recipe_eval is not None and recipe_eval.blocked:
            _print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=False)
            raise click.ClickException(
                "Recipe step failed. Run `desk ami build step --retry`, or "
                "`desk ami build cancel` to archive this build."
            )

        if (
            snap.recorded_instance_id
            and recipe_eval is not None
            and recipe_eval.in_progress_step_index is not None
            and recipe_eval.in_progress_command_id
        ):
            steps = _get_build_steps(snap.config)
            idx = recipe_eval.in_progress_step_index
            cur = steps[idx]
            ok = _poll_ssm_invocation_compact(
                snap.recorded_instance_id,
                recipe_eval.in_progress_command_id,
                step_index=idx,
                total_steps=len(steps),
                step_detail=_describe_recipe_step_for_status(cur),
                region=region,
                profile=profile,
            )
            if not ok:
                raise click.ClickException(
                    "Recipe step failed. Run `desk ami build step --retry`, or "
                    "`desk ami build cancel` to archive this build."
                )
            continue

        _print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=False)
        _run_async_ami_build_step(
            snap,
            recipe_eval=recipe_eval,
            retry=False,
            no_wait=no_wait,
        )
        time.sleep(2)


def _safe_get_instance_state(
    instance_id: str,
    *,
    region: str | None,
    profile: str | None,
) -> str | None:
    try:
        return get_instance_state(instance_id, region=region, profile=profile)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "InvalidInstanceID.NotFound":
            return None
        raise


def _async_ami_image_id_from_result(ami_result: dict[str, Any] | None) -> str | None:
    if not ami_result:
        return None
    raw = ami_result.get("image_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _resolve_async_ami_build_snapshot(build_id: str, *, stack: str) -> AsyncAmiBuildSnapshot:
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    bid = _normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    prefix = f"{AMI_BUILDS_PREFIX}{bid}/"
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    cfg_key = f"{prefix}config.json"
    config = _read_s3_object_json(s3, bucket, cfg_key)
    if config is None:
        raise click.ClickException(
            f"No staged AMI build found for id {bid!r} "
            f"(missing {AMI_BUILDS_PREFIX}{bid}/config.json)."
        )

    builder_key = f"{prefix}{BUILDER_INSTANCE_KEY}"
    builder_doc = _read_s3_object_json(s3, bucket, builder_key)
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
        ec2_state = _safe_get_instance_state(recorded_id, region=region, profile=profile)
        if ec2_state is None:
            ec2_missing = True
        elif ec2_state in ("running", "pending"):
            ssm_ready = is_ssm_ready(recorded_id, region=region, profile=profile)
        elif ec2_state in ("stopped", "stopping", "shutting-down"):
            ssm_ready = False
        elif ec2_state == "terminated":
            ssm_ready = None

    result_key = f"{prefix}{AMI_RESULT_KEY}"
    ami_result = _read_s3_object_json(s3, bucket, result_key)

    reg_image_id = _async_ami_image_id_from_result(ami_result)
    reg_ami_state: str | None = None
    if reg_image_id:
        reg_ami_state = get_ami_state(reg_image_id, region=region, profile=profile)

    # Completion: AMI available and builder gone, or legacy ami-result.json pipeline_complete flag.
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


def _ami_build_comment_tag(build_id: str, step_index: int, kind: str) -> str:
    """SSM Comment value correlating an invocation to a recipe step (AWS max 100 chars)."""
    bid = _normalize_build_id_arg(build_id)
    base = f"{AMI_BUILD_COMMENT_PREFIX}{bid}:{step_index}:{kind}"
    if len(base) <= 100:
        return base
    short = hashlib.sha256(bid.encode()).hexdigest()[:12]
    return f"{AMI_BUILD_COMMENT_PREFIX}{short}:{step_index}:{kind}"


def _parse_ami_build_comment(comment: str | None, build_id: str) -> tuple[int, str] | None:
    if not comment or not comment.startswith(AMI_BUILD_COMMENT_PREFIX):
        return None
    bid = _normalize_build_id_arg(build_id)
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


def _normalize_shell_for_compare(cmd: str) -> str:
    return " ".join(cmd.split())


def _staged_s3_object_key(src: str) -> str:
    s = src.strip()
    if not s.startswith("s3:/"):
        raise click.ClickException(
            f"Staged copy source must be s3:/… (got {src!r}). Re-run `desk ami build create`."
        )
    return s[4:].lstrip("/")


def _tar_member_name_for_single_file(source: str, dest: str) -> str:
    """Path inside the tarball for a single-file copy (matches final basename on extract)."""
    if dest.endswith("/") or dest.endswith(os.sep):
        return os.path.basename(source)
    d = dest.rstrip("/")
    base = os.path.basename(d)
    if not base:
        return os.path.basename(source)
    return base


def _parent_dir_for_file_copy_dest(dest: str) -> str:
    """Directory to pass to ``tar -C`` for a single-file copy (handles ``…/`` targets)."""
    if dest.endswith("/") or dest.endswith(os.sep):
        return dest.rstrip("/") or "."
    d = os.path.dirname(dest)
    return d if d else "."


def _write_ami_copy_tarball(
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
                arc = _tar_member_name_for_single_file(resolved, dest)
                tf.add(os.path.abspath(resolved), arcname=arc, recursive=False)
        return tmp_path
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _async_shell_for_copy_step(
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
    key = _staged_s3_object_key(src)
    url = generate_presigned_get_object_url(
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
        dest_dir = _parent_dir_for_file_copy_dest(raw_dest)
        lines.append(f"install -d -m 0755 {shlex.quote(dest_dir)}")
        lines.append(f'tar -xf "$TMP" -C {shlex.quote(dest_dir)}')
    return "\n".join(lines) + "\n"


def _async_shell_for_run_step(
    run_value: str,
    step_index: int,
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    rv = run_value.strip()
    if rv.startswith("s3:/"):
        key = _staged_s3_object_key(rv)
        tmp = f"/tmp/desk-ami-run-{step_index}.sh"
        url = generate_presigned_get_object_url(
            bucket, key, region=region, profile=profile
        )
        return (
            "set -eu\n"
            f"curl -fsSL {shlex.quote(url)} -o {shlex.quote(tmp)}\n"
            f"bash {shlex.quote(tmp)}\n"
        )
    return rv


def _expected_async_shell_for_step(
    step: dict[str, Any],
    step_index: int,
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    if "run" in step:
        return _async_shell_for_run_step(
            step["run"], step_index, bucket=bucket, region=region, profile=profile
        )
    return _async_shell_for_copy_step(
        step["copy"], bucket=bucket, region=region, profile=profile
    )


@dataclass(frozen=True)
class AsyncRecipeEval:
    """Derived from SSM Run Command history for the builder instance."""

    total_steps: int
    steps: tuple[dict[str, Any], ...]
    blocked: bool
    blocked_step_index: int | None
    blocked_command_id: str | None
    last_error: str | None
    in_progress_step_index: int | None
    in_progress_command_id: str | None
    next_step_index: int | None
    recipe_complete: bool


def _invocation_step_failed(status: str, exit_code: int | None) -> bool:
    if status in ("Failed", "TimedOut", "Cancelled", "Cancelling"):
        return True
    if status == "Success":
        if exit_code is None:
            return False
        return exit_code != 0
    return False


def _invocation_step_succeeded(status: str, exit_code: int | None) -> bool:
    return status == "Success" and (exit_code is None or exit_code == 0)


def _poll_ssm_invocation_compact(
    instance_id: str,
    command_id: str,
    *,
    step_index: int,
    total_steps: int,
    step_detail: str,
    region: str | None,
    profile: str | None,
) -> bool:
    """Wait for SSM command to finish; compact one-line progress. Returns True if succeeded."""
    terminal_states = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    spin_i = 0
    tty = _stdout_is_tty()
    detail = _truncate_status_text(step_detail, max_len=120)
    label = f"{_GROUP_BUILD}  [{step_index + 1}/{total_steps}] {detail}"
    try:
        while True:
            result = get_command_invocation(
                command_id, instance_id, region=region, profile=profile
            )
            if result.status in terminal_states:
                break
            if tty:
                fr = _SPINNER_FRAMES[spin_i % len(_SPINNER_FRAMES)]
                spin_i += 1
                msg = f"{fr} {label}"
                w = max(20, shutil.get_terminal_size((80, 20)).columns)
                pad = msg + " " * max(0, w - len(msg) - 1)
                click.echo("\r" + pad[:w], nl=False)
            time.sleep(1)
    finally:
        if tty:
            click.echo()

    inv = get_command_invocation(
        command_id, instance_id, region=region, profile=profile
    )
    if _invocation_step_succeeded(inv.status, inv.exit_code):
        _compact_line_ok(_GROUP_BUILD, f"[{step_index + 1}/{total_steps}] {detail}")
        return True

    _compact_line_fail(_GROUP_BUILD, f"[{step_index + 1}/{total_steps}] {detail}")
    _print_full_invocation_io(
        instance_id, command_id, region=region, profile=profile
    )
    return False


def _map_invocation_to_step_index(
    command_id: str,
    *,
    build_id: str,
    steps: list[dict[str, Any]],
    bucket: str,
    region: str | None,
    profile: str | None,
) -> int | None:
    try:
        cmd_doc = get_ssm_command(command_id, region=region, profile=profile)
    except (ClientError, RuntimeError):
        return None
    if cmd_doc.get("DocumentName") != "AWS-RunShellScript":
        return None
    params = cmd_doc.get("Parameters") or {}
    commands = params.get("commands")
    if not commands or not isinstance(commands, list):
        return None
    shell = commands[0] if commands else ""
    parsed = _parse_ami_build_comment(cmd_doc.get("Comment"), build_id)
    if parsed is not None:
        return parsed[0]
    norm = _normalize_shell_for_compare(shell)
    for i, step in enumerate(steps):
        try:
            expected = _expected_async_shell_for_step(
                step, i, bucket=bucket, region=region, profile=profile
            )
        except click.ClickException:
            continue
        if norm == _normalize_shell_for_compare(expected):
            return i
    return None


def _evaluate_async_recipe(
    instance_id: str,
    *,
    build_id: str,
    config: dict[str, Any],
    bucket: str,
    region: str | None,
    profile: str | None,
) -> AsyncRecipeEval:
    steps = _get_build_steps(config)
    n = len(steps)
    if n == 0:
        return AsyncRecipeEval(
            total_steps=0,
            steps=tuple(),
            blocked=False,
            blocked_step_index=None,
            blocked_command_id=None,
            last_error=None,
            in_progress_step_index=None,
            in_progress_command_id=None,
            next_step_index=None,
            recipe_complete=True,
        )

    inv_rows = list_command_invocations_for_instance(
        instance_id, region=region, profile=profile
    )
    # Latest invocation wins per step index (RequestedDateTime ascending list).
    by_step: dict[int, dict[str, Any]] = {}
    for row in inv_rows:
        cid = row.get("CommandId")
        if not cid:
            continue
        step_i = _map_invocation_to_step_index(
            cid,
            build_id=build_id,
            steps=steps,
            bucket=bucket,
            region=region,
            profile=profile,
        )
        if step_i is None:
            continue
        prev = by_step.get(step_i)
        if prev is None or (row.get("RequestedDateTime") or "") >= (
            prev.get("RequestedDateTime") or ""
        ):
            by_step[step_i] = dict(row)

    terminal = ("Success", "Failed", "TimedOut", "Cancelled", "Cancelling")

    def enrich(row: dict[str, Any]) -> tuple[str, int | None, str]:
        st = row.get("Status") or ""
        cid = row.get("CommandId") or ""
        exit_code: int | None = None
        stderr = ""
        if cid and st in terminal:
            try:
                inv = get_command_invocation(
                    cid, instance_id, region=region, profile=profile
                )
                exit_code = inv.exit_code
                stderr = inv.stderr or ""
            except ClientError:
                pass
        return st, exit_code, stderr

    for i in range(n):
        row = by_step.get(i)
        if row is None:
            return AsyncRecipeEval(
                total_steps=n,
                steps=tuple(steps),
                blocked=False,
                blocked_step_index=None,
                blocked_command_id=None,
                last_error=None,
                in_progress_step_index=None,
                in_progress_command_id=None,
                next_step_index=i,
                recipe_complete=False,
            )
        st, exit_code, stderr = enrich(row)
        cid = row.get("CommandId")
        if st in ("Pending", "InProgress", "Delayed", "PendingDeletion"):
            return AsyncRecipeEval(
                total_steps=n,
                steps=tuple(steps),
                blocked=False,
                blocked_step_index=None,
                blocked_command_id=None,
                last_error=None,
                in_progress_step_index=i,
                in_progress_command_id=cid if isinstance(cid, str) else None,
                next_step_index=None,
                recipe_complete=False,
            )
        if _invocation_step_failed(st, exit_code):
            detail = f"status={st!r}"
            if exit_code is not None:
                detail += f" exit_code={exit_code}"
            if stderr.strip():
                detail += f" stderr={stderr.strip()[:2000]}"
            return AsyncRecipeEval(
                total_steps=n,
                steps=tuple(steps),
                blocked=True,
                blocked_step_index=i,
                blocked_command_id=cid if isinstance(cid, str) else None,
                last_error=detail,
                in_progress_step_index=None,
                in_progress_command_id=None,
                next_step_index=None,
                recipe_complete=False,
            )
        if not _invocation_step_succeeded(st, exit_code):
            return AsyncRecipeEval(
                total_steps=n,
                steps=tuple(steps),
                blocked=False,
                blocked_step_index=None,
                blocked_command_id=None,
                last_error=None,
                in_progress_step_index=i,
                in_progress_command_id=cid if isinstance(cid, str) else None,
                next_step_index=None,
                recipe_complete=False,
            )

    return AsyncRecipeEval(
        total_steps=n,
        steps=tuple(steps),
        blocked=False,
        blocked_step_index=None,
        blocked_command_id=None,
        last_error=None,
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=None,
        recipe_complete=True,
    )


def _maybe_evaluate_async_recipe(snap: AsyncAmiBuildSnapshot) -> AsyncRecipeEval | None:
    """Load SSM recipe state when the builder can run steps; otherwise return None.

    Used so `ami build step` can evaluate once, print status, and advance using the same data.
    """
    if not snap.recorded_instance_id:
        return None
    if snap.ec2_missing or snap.ec2_state == "terminated":
        return None
    if snap.ec2_state not in ("running", "pending"):
        return None
    if snap.ssm_ready is not True:
        return None
    aws = get_desk_settings().aws_settings
    return _evaluate_async_recipe(
        snap.recorded_instance_id,
        build_id=snap.build_id,
        config=snap.config,
        bucket=snap.bucket,
        region=aws.region,
        profile=aws.profile,
    )


def _print_async_post_recipe_section_verbose(snap: AsyncAmiBuildSnapshot) -> None:
    """Post-recipe AMI registration lines (after recipe steps are done).

    Uses only fields on ``snap`` (AMI state is resolved once in `_resolve_async_ami_build_snapshot`).
    """
    click.echo("  Post-recipe (AMI):")
    image_id = snap.registered_ami_id

    if snap.async_pipeline_fully_complete and image_id:
        click.echo(f"    Pipeline: complete (registered {image_id}).")
        return

    if not image_id:
        click.echo(
            "    Next: `desk ami build step` creates the AMI from the builder, then (when the "
            "AMI is available) terminates the builder."
        )
        return

    st = snap.registered_ami_state
    click.echo(f"    Image: {image_id}")
    click.echo(f"    AMI state (AWS): {st or 'unknown'}")
    if st == "available":
        click.echo(
            "    Next: `desk ami build step` will terminate the builder instance "
            "(no long waits in this command)."
        )
    elif st in ("failed", "error", "deregistered") or st is None:
        click.echo("    AMI creation did not succeed; fix the problem in AWS or `desk ami build cancel`.")
    else:
        click.echo(
            "    Next: run `desk ami build status` or `step` again when the AMI is available."
        )


def _print_async_post_recipe_compact(snap: AsyncAmiBuildSnapshot) -> None:
    """One-line post-recipe AMI registration / terminate (compact default output)."""
    image_id = snap.registered_ami_id
    iid = snap.recorded_instance_id or "?"

    if snap.async_pipeline_fully_complete and image_id:
        _compact_line_ok(_GROUP_REGISTER, image_id)
        _compact_line_ok(_GROUP_TERMINATE, f"complete  ({iid})")
        return

    if not image_id:
        _compact_line_pending(_GROUP_REGISTER, "next: `desk ami build step`")
        return

    st = snap.registered_ami_state
    if st == "available":
        _compact_line_ok(_GROUP_WAIT_AMI, f"{image_id}  available")
        _compact_line_pending(_GROUP_TERMINATE, f"{iid}  (`desk ami build step`)")
    elif st in ("failed", "error", "deregistered") or st is None:
        _compact_line_fail(_GROUP_WAIT_AMI, f"{image_id}  state={st!r}")
    else:
        _compact_line_working(
            _GROUP_WAIT_AMI,
            f"{image_id}  state={st or 'unknown'}",
        )


def _print_async_ami_build_status_compact(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
) -> None:
    """Concise one-line-per-phase status (default)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    ws_name = _workstation_name_for_staged_build(snap.build_id)
    _compact_line_ok(
        _GROUP_STAGE,
        f"{snap.build_id}  s3://{snap.bucket}/{snap.s3_prefix}",
    )

    if not snap.recorded_instance_id:
        _compact_line_pending(
            _GROUP_LAUNCH,
            f"{ws_name}  (`desk ami build step` creates builder)",
        )
        return

    if snap.ec2_missing:
        _compact_line_fail(_GROUP_LAUNCH, "instance no longer in EC2")
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_compact(snap)
        else:
            click.echo(
                "  Builder was removed from EC2; step will not launch a replacement automatically."
            )
        return

    assert snap.ec2_state is not None
    _compact_line_ok(_GROUP_LAUNCH, f"{ws_name}  {snap.recorded_instance_id}")

    if snap.ec2_state == "terminated":
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_compact(snap)
        else:
            click.echo(
                "  Builder terminated; step will not launch a replacement automatically."
            )
        return

    if snap.ec2_state in ("running", "pending"):
        if snap.ssm_ready is True:
            _compact_line_ok(_GROUP_WAIT_SSM, snap.recorded_instance_id)
        elif snap.ssm_ready is False:
            _compact_line_working(
                _GROUP_WAIT_SSM,
                f"{snap.recorded_instance_id}  (poll `desk ami build status`)",
            )
        else:
            _compact_line_working(
                _GROUP_WAIT_SSM,
                f"{snap.recorded_instance_id}  (n/a)",
            )

    if snap.ec2_state in ("running", "pending") and snap.ssm_ready is True:
        assert snap.recorded_instance_id is not None
        ev = recipe_eval if recipe_eval is not None else _maybe_evaluate_async_recipe(snap)
        if ev is None:
            return
        steps = _get_build_steps(snap.config)
        n = ev.total_steps
        if n == 0:
            _compact_line_ok(_GROUP_BUILD, "(no recipe steps)")
            _print_async_post_recipe_compact(snap)
        elif ev.recipe_complete:
            for i in range(n):
                _compact_line_ok(
                    _GROUP_BUILD,
                    f"[{i + 1}/{n}] {_describe_recipe_step_for_status(steps[i])}",
                )
            _print_async_post_recipe_compact(snap)
        elif ev.blocked and ev.blocked_step_index is not None:
            bi = ev.blocked_step_index
            for i in range(bi):
                _compact_line_ok(
                    _GROUP_BUILD,
                    f"[{i + 1}/{n}] {_describe_recipe_step_for_status(steps[i])}",
                )
            bad = steps[bi]
            _compact_line_fail(
                _GROUP_BUILD,
                f"[{bi + 1}/{n}] {_describe_recipe_step_for_status(bad)}",
            )
            if ev.last_error:
                click.echo(f"  {ev.last_error}")
            if ev.blocked_command_id:
                _print_full_invocation_io(
                    snap.recorded_instance_id,
                    ev.blocked_command_id,
                    region=region,
                    profile=profile,
                )
            click.echo(
                "  Hint: `desk ami build step --retry` or `desk ami build cancel`.",
            )
        elif ev.in_progress_step_index is not None:
            ii = ev.in_progress_step_index
            for i in range(ii):
                _compact_line_ok(
                    _GROUP_BUILD,
                    f"[{i + 1}/{n}] {_describe_recipe_step_for_status(steps[i])}",
                )
            cur = steps[ii]
            _compact_line_working(
                _GROUP_BUILD,
                f"[{ii + 1}/{n}] {_describe_recipe_step_for_status(cur)}",
            )
        elif ev.next_step_index is not None:
            ni = ev.next_step_index
            for i in range(ni):
                _compact_line_ok(
                    _GROUP_BUILD,
                    f"[{i + 1}/{n}] {_describe_recipe_step_for_status(steps[i])}",
                )
            nxt = steps[ni]
            _compact_line_pending(
                _GROUP_BUILD,
                f"[{ni + 1}/{n}] {_describe_recipe_step_for_status(nxt)}  (`desk ami build step`)",
            )
    elif snap.ec2_state in ("stopped", "stopping", "shutting-down"):
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_compact(snap)
        else:
            click.echo(
                "  Builder not running; fix the instance or archive the build.",
            )


def _print_async_ami_build_status_verbose(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    verbose: bool = True,
) -> None:
    """Verbose multi-line status (``--verbose``)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    ami_name = snap.config.get("ami_name", "-")
    click.echo(f"AMI build: {snap.build_id}")
    click.echo(f"  Staged config: present (ami_name={ami_name!r})")
    click.echo(f"  s3://{snap.bucket}/{snap.s3_prefix}")

    if not snap.recorded_instance_id:
        click.echo("  Builder instance id (S3): (not recorded yet)")
        click.echo("  EC2: (no instance recorded for this build)")
        click.echo("  SSM ready: n/a")
        click.echo()
        click.echo(
            "Next: `desk ami build step` will create the builder instance, "
            f"then write {BUILDER_INSTANCE_KEY} to the build prefix in S3."
        )
        return

    click.echo(f"  Builder instance id (S3): {snap.recorded_instance_id}")

    if snap.ec2_missing:
        click.echo("  EC2: instance not found (no longer visible to DescribeInstances)")
        click.echo("  SSM ready: n/a")
        click.echo()
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_section_verbose(snap)
        else:
            click.echo(
                "The builder instance for this build was created earlier but is no longer "
                "present in EC2. `desk ami build step` will not launch a replacement automatically."
            )
        return

    assert snap.ec2_state is not None
    click.echo(f"  EC2 state: {snap.ec2_state}")

    if snap.ec2_state == "terminated":
        click.echo("  SSM ready: n/a")
        click.echo()
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_section_verbose(snap)
        else:
            click.echo(
                "The builder instance for this build has terminated. "
                "`desk ami build step` will not launch a replacement automatically."
            )
        return

    if snap.ssm_ready is None:
        click.echo("  SSM ready: n/a")
    else:
        click.echo(f"  SSM ready: {'yes' if snap.ssm_ready else 'no'}")

    click.echo()
    if snap.ec2_state in ("running", "pending") and snap.ssm_ready is True:
        assert snap.recorded_instance_id is not None
        ev = recipe_eval if recipe_eval is not None else _maybe_evaluate_async_recipe(snap)
        if ev is None:
            return
        steps = _get_build_steps(snap.config)
        click.echo("  Recipe:")
        click.echo(f"    Steps in config: {ev.total_steps}")
        if ev.recipe_complete:
            click.echo("    State: all steps completed successfully.")
            if ev.total_steps > 0:
                last = steps[ev.total_steps - 1]
                click.echo(
                    f"    Last completed: step {ev.total_steps - 1} — "
                    f"{_describe_recipe_step_for_status(last)}"
                )
            click.echo()
            _print_async_post_recipe_section_verbose(snap)
        elif ev.blocked and ev.blocked_step_index is not None:
            bad = steps[ev.blocked_step_index]
            click.echo(
                f"    State: failed at step {ev.blocked_step_index} — "
                f"{_describe_recipe_step_for_status(bad)}"
            )
            click.echo(
                "    Hint: `desk ami build step --retry` or `desk ami build cancel`."
            )
            if ev.last_error:
                click.echo(f"    Last error: {ev.last_error}")
            if verbose:
                _print_verbose_recipe_command_io(snap, ev, region=region, profile=profile)
        elif ev.in_progress_step_index is not None:
            cur = steps[ev.in_progress_step_index]
            click.echo(
                f"    State: step {ev.in_progress_step_index} in progress — "
                f"{_describe_recipe_step_for_status(cur)}"
            )
            click.echo(f"    SSM command_id: {ev.in_progress_command_id!r}")
            if ev.in_progress_step_index > 0:
                prev = steps[ev.in_progress_step_index - 1]
                click.echo(
                    f"    Last completed: step {ev.in_progress_step_index - 1} — "
                    f"{_describe_recipe_step_for_status(prev)}"
                )
            if verbose:
                _print_verbose_recipe_command_io(snap, ev, region=region, profile=profile)
        elif ev.next_step_index is not None:
            nxt = steps[ev.next_step_index]
            click.echo(
                f"    Next: step {ev.next_step_index} — "
                f"{_describe_recipe_step_for_status(nxt)}"
            )
            click.echo("    (`desk ami build step` to start it.)")
            if ev.next_step_index > 0:
                prev = steps[ev.next_step_index - 1]
                click.echo(
                    f"    Last completed: step {ev.next_step_index - 1} — "
                    f"{_describe_recipe_step_for_status(prev)}"
                )
    elif snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
        click.echo(
            "Waiting for SSM on the builder instance. "
            "Run `desk ami build status` or `step` again later (no long waits in this command)."
        )
    elif snap.ec2_state in ("stopped", "stopping", "shutting-down"):
        click.echo()
        if snap.async_pipeline_fully_complete:
            _print_async_post_recipe_section_verbose(snap)
        else:
            click.echo(
                "The builder instance is not in a running state; fix the instance or terminate "
                "and archive the build."
            )


def _print_async_ami_build_status(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    verbose: bool = False,
) -> None:
    """Human-readable status for staged AMI build (also used at the start of `step`).

    Pass ``recipe_eval`` from `ami build step` so status matches the step logic (single fetch).
    Default is compact one-line phases; ``--verbose`` selects the legacy multi-line view.
    """
    if verbose:
        _print_async_ami_build_status_verbose(snap, recipe_eval=recipe_eval, verbose=True)
    else:
        _print_async_ami_build_status_compact(snap, recipe_eval=recipe_eval)


def _run_async_ami_build_step(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    retry: bool = False,
    no_wait: bool = False,
) -> None:
    """Perform at most one quick action for `desk ami build step` (after status output).

    When the builder is running and SSM-ready, ``recipe_eval`` must be supplied (same object
    as for `_print_async_ami_build_status`); this function does not re-query AWS for recipe state.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    if snap.recorded_instance_id:
        if snap.ec2_missing or snap.ec2_state == "terminated":
            if snap.async_pipeline_fully_complete:
                click.echo()
                click.secho("AMI build pipeline already complete.", fg="green")
                return
            raise click.ClickException(
                "Refusing to create a new builder instance: this build already recorded "
                f"{snap.recorded_instance_id!r}, and that instance is no longer usable. "
                "Use `desk ami build cancel` or investigate in AWS."
            )
        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is True:
            assert snap.recorded_instance_id is not None
            if recipe_eval is None:
                raise click.ClickException(
                    "Internal error: recipe evaluation was not provided for an SSM-ready builder; "
                    "this is a desk bug."
                )
            ev = recipe_eval
            if retry:
                if ev.recipe_complete:
                    raise click.ClickException(
                        "Nothing to retry: all recipe steps already completed successfully."
                    )
                if ev.in_progress_step_index is not None:
                    raise click.ClickException(
                        f"Cannot use --retry while step {ev.in_progress_step_index} "
                        "is still in progress on SSM."
                    )
                if not ev.blocked or ev.blocked_step_index is None:
                    raise click.ClickException(
                        "Nothing to retry: there is no failed step (see `desk ami build status`)."
                    )
                steps = _get_build_steps(snap.config)
                step = steps[ev.blocked_step_index]
                kind = "run" if "run" in step else "copy"
                shell = _expected_async_shell_for_step(
                    step,
                    ev.blocked_step_index,
                    bucket=snap.bucket,
                    region=region,
                    profile=profile,
                )
                comment = _ami_build_comment_tag(snap.build_id, ev.blocked_step_index, kind)
                command_id = send_ssm_command(
                    snap.recorded_instance_id,
                    shell,
                    region=region,
                    profile=profile,
                    timeout_seconds=7200,
                    comment=comment,
                )
                _compact_line_ok(
                    _GROUP_BUILD,
                    f"retry step {ev.blocked_step_index} ({kind})  command_id={command_id}",
                )
                click.echo("  (Not waiting; check `desk ami build status`.)")
                return
            if ev.blocked:
                _compact_line_pending(
                    "Next action",
                    "`desk ami build step --retry` or `desk ami build cancel` (no step taken)",
                )
                return
            if ev.in_progress_step_index is not None:
                _compact_line_pending(
                    _GROUP_BUILD,
                    f"step {ev.in_progress_step_index} still in progress on SSM (no step taken)",
                )
                return
            if ev.recipe_complete:
                session = boto3.Session(region_name=region, profile_name=profile)
                s3 = session.client("s3")
                if snap.async_pipeline_fully_complete:
                    click.echo()
                    click.secho("AMI build pipeline already complete.", fg="green")
                    return
                image_id = snap.registered_ami_id
                if not image_id:
                    ami_name = snap.config.get("ami_name")
                    if not ami_name or not isinstance(ami_name, str):
                        raise click.ClickException("Config must specify 'ami_name'.")
                    reg_name = _registration_ami_name_for_async_build(ami_name.strip(), snap.build_id)
                    new_image_id = create_ami(
                        snap.recorded_instance_id,
                        name=reg_name,
                        description=f"desk async AMI build {snap.build_id}",
                        no_reboot=False,
                        region=region,
                        profile=profile,
                    )
                    _merge_ami_result_s3(
                        s3,
                        snap.bucket,
                        snap.s3_prefix,
                        {"image_id": new_image_id},
                    )
                    _compact_line_ok(
                        _GROUP_REGISTER,
                        f"{new_image_id}  name={reg_name!r}",
                    )
                    click.echo(
                        "  (Not waiting for availability; check `desk ami build status`, then `step` "
                        "when the AMI is available to terminate the builder.)"
                    )
                    return
                st = snap.registered_ami_state
                if st in ("failed", "error", "deregistered") or st is None:
                    raise click.ClickException(
                        f"AMI {image_id} is not usable (state={st!r}). See AWS console or cancel this build."
                    )
                if st != "available":
                    if (
                        no_wait
                        and snap.recorded_instance_id
                        and st not in ("failed", "error", "deregistered")
                    ):
                        _merge_ami_result_s3(
                            s3,
                            snap.bucket,
                            snap.s3_prefix,
                            {"pipeline_complete": True},
                        )
                        terminate_instance(
                            snap.recorded_instance_id, region=region, profile=profile
                        )
                        click.echo()
                        click.secho(
                            f"Terminated builder {snap.recorded_instance_id} (--no-wait; "
                            f"AMI {image_id} was {st!r}).",
                            fg="yellow",
                        )
                        return
                    click.echo()
                    click.echo(
                        f"(No step taken: AMI {image_id} is still {st!r}; run `desk ami build step` "
                        "again when it is available.)"
                    )
                    return
                terminate_instance(snap.recorded_instance_id, region=region, profile=profile)
                click.echo()
                click.secho(
                    f"Terminated builder {snap.recorded_instance_id}; AMI {image_id} is available.",
                    fg="green",
                )
                return
            if ev.next_step_index is None:
                click.echo()
                click.echo("(No step taken.)")
                return
            steps = _get_build_steps(snap.config)
            step = steps[ev.next_step_index]
            kind = "run" if "run" in step else "copy"
            shell = _expected_async_shell_for_step(
                step,
                ev.next_step_index,
                bucket=snap.bucket,
                region=region,
                profile=profile,
            )
            comment = _ami_build_comment_tag(snap.build_id, ev.next_step_index, kind)
            command_id = send_ssm_command(
                snap.recorded_instance_id,
                shell,
                region=region,
                profile=profile,
                timeout_seconds=7200,
                comment=comment,
            )
            _compact_line_ok(
                _GROUP_BUILD,
                f"started step {ev.next_step_index} ({kind})  command_id={command_id}",
            )
            click.echo("  (Not waiting; check `desk ami build status`.)")
            return
        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
            _compact_line_pending(_GROUP_WAIT_SSM, "(no step taken; waiting for SSM)")
            return
        if snap.ec2_state in ("stopped", "stopping", "shutting-down"):
            click.echo()
            if snap.async_pipeline_fully_complete:
                click.secho(
                    "AMI build pipeline complete (builder stopped or shutting down).",
                    fg="green",
                )
            else:
                click.echo("(No step taken: instance not running.)")
            return
        raise click.ClickException(
            f"Unexpected builder state (ec2_state={snap.ec2_state!r}, "
            f"ec2_missing={snap.ec2_missing})."
        )

    instance_type = snap.config.get("instance_type", "t3.medium")
    workstation_name = _workstation_name_for_staged_build(snap.build_id)
    builder_ami = get_latest_ubuntu_ami(region=region, profile=profile)
    _compact_line_working(_GROUP_LAUNCH, f"creating {workstation_name!r}…")
    try:
        instance_id, _shutdown = create_workstation(
            workstation_name,
            instance_type,
            ami_id=builder_ami,
            shutdown_after="4h",
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    key = f"{snap.s3_prefix}{BUILDER_INSTANCE_KEY}"
    _put_s3_object_json(
        s3,
        snap.bucket,
        key,
        {"instance_id": instance_id},
    )
    _compact_line_ok(
        _GROUP_LAUNCH,
        f"{workstation_name}  {instance_id}  s3://{snap.bucket}/{key}",
    )


def _validate_build_recipe_config(config: dict[str, Any], config_path: str) -> None:
    if "base_ami" in config:
        raise click.ClickException(
            "Builder always uses latest Ubuntu 24.04; 'base_ami' is not allowed in recipes."
        )
    ami_name = config.get("ami_name")
    if not ami_name:
        raise click.ClickException("Config must specify 'ami_name'.")
    if "workstation_name" in config:
        raise click.ClickException(
            "Config must not specify 'workstation_name'; it is auto-generated from ami_name."
        )
    if "key" in config:
        raise click.ClickException("Config must not specify 'key'.")
    _ = config_path  # reserved for future path-based checks


@ami_group.group("build")
def ami_build_group() -> None:
    """Stage AMI build recipes in S3 and run the builder pipeline."""
    pass


@ami_build_group.command("status")
@click.argument("build_id")
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Multi-line status with SSM command script and invocation stdout/stderr for active/failed step.",
)
def ami_build_status(build_id: str, stack: str, verbose: bool) -> None:
    """Show staged AMI build progress from S3, EC2, and SSM Run Command history (quick; does not wait).

    Default output is one line per phase; use --verbose for the legacy detailed view. Recipe
    progress is derived from SSM commands on the builder instance (Comment tag and/or command
    body match). After a step fails, use `desk ami build step --retry` or archive with
    `desk ami build cancel` before staging a new build.
    """
    snap = _resolve_async_ami_build_snapshot(build_id, stack=stack)
    recipe_eval = _maybe_evaluate_async_recipe(snap)
    _print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=verbose)


@ami_build_group.command("step")
@click.argument("build_id")
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
@click.option(
    "--retry",
    is_flag=True,
    help="After a failed recipe step, re-send that step's SSM command (new presigned URLs).",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Multi-line status with SSM command script and invocation stdout/stderr for active/failed step.",
)
def ami_build_step(build_id: str, stack: str, retry: bool, verbose: bool) -> None:
    """Advance the async AMI build by one quick action, or no-op if there is nothing to do.

    Resolves S3/EC2/SSM once, evaluates recipe state once, prints status from that snapshot,
    then applies the same snapshot to decide how to step. Creates the builder instance
    (recording its id in S3) when needed. When SSM is ready, starts at most one recipe
    ``run``/``copy`` step via SSM and returns immediately after ``SendCommand`` (does not
    wait for the remote command). Skips if a prior step failed (use ``--retry`` or
    ``cancel``) or a step is still in progress on SSM. After all recipe steps succeed, creates
    the AMI from the builder (then terminates the builder once the AMI is available).
    """
    snap = _resolve_async_ami_build_snapshot(build_id, stack=stack)
    recipe_eval = _maybe_evaluate_async_recipe(snap)
    _print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=verbose)
    _run_async_ami_build_step(snap, recipe_eval=recipe_eval, retry=retry)


@ami_build_group.command("run")
@click.argument(
    "config_file",
    required=False,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--continue",
    "resume_build_id",
    metavar="BUILD_ID",
    default=None,
    help="Resume a staged build under ami-builds/<id>/ (e.g. after Ctrl-C).",
)
@click.option(
    "--no-wait",
    is_flag=True,
    help="Do not wait for AMI to become available before terminating the builder (legacy behavior).",
)
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def ami_build_run(
    config_file: Optional[str],
    resume_build_id: Optional[str],
    no_wait: bool,
    stack: str,
) -> None:
    """Stage a recipe to S3, drive ``step`` until the pipeline completes, then archive the build.

    CONFIG_FILE is a JSON file with:
      instance_type (optional): e.g. t3.medium (default: t3.medium).
      The builder always starts from the latest Ubuntu 24.04 LTS AMI.
      steps: list of steps; each step is {\"run\": \"cmd\"} or {\"copy\": {\"source\": \"...\", \"dest\": \"...\", \"recursive\": optional}}.
      Alternatively (legacy) copy + run + optional run_before_copy.
      ami_name: base name for the registered AMI (async builds append the build id for uniqueness).

    This command uses the same S3 + SSM pipeline as ``desk ami build create`` / ``step`` (not
    direct SCP). While recipe steps run, progress is shown as compact lines (spinner on TTYs).
    On success the staged prefix is moved to ami-build-archive/. On failure the build is left
    under ami-builds/ for debugging.

    Use ``--continue BUILD_ID`` to resume after an interrupt without re-uploading artifacts.
    """
    if resume_build_id and config_file:
        raise click.UsageError("Pass either CONFIG_FILE or --continue BUILD_ID, not both.")
    if not resume_build_id and not config_file:
        raise click.UsageError("Missing CONFIG_FILE (or use --continue BUILD_ID).")

    bid = ""
    try:
        if resume_build_id:
            bid = _normalize_build_id_arg(resume_build_id)
            if not bid:
                raise click.ClickException("Build id is empty.")
            ws = _workstation_name_for_staged_build(bid)
            _compact_line_ok(
                _GROUP_STAGE,
                f"resume {bid}  s3://{AMI_BUILDS_PREFIX}{bid}/  builder {ws}",
            )
            _drive_ami_build_run_loop(bid, stack=stack, no_wait=no_wait)
        else:
            assert config_file is not None
            build_id, bucket, prefix = _stage_ami_build_to_s3(config_file, stack=stack)
            bid = build_id
            ws = _workstation_name_for_staged_build(build_id)
            _compact_line_ok(
                _GROUP_STAGE,
                f"{build_id}  s3://{bucket}/{prefix}  builder {ws}  config {config_file}",
            )
            _drive_ami_build_run_loop(build_id, stack=stack, no_wait=no_wait)
    except KeyboardInterrupt:
        click.echo()
        if bid:
            click.secho(
                f"Interrupted. Resume with: desk ami build run --continue {bid}",
                fg="yellow",
            )
        raise SystemExit(130) from None


@ami_build_group.command("create")
@click.argument(
    "config_file",
    type=click.Path(exists=True),
)
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def ami_build_create(config_file: str, stack: str) -> None:
    """Upload an AMI build recipe and local artifacts to a dedicated folder in the desk S3 bucket.

    Writes a normalized config (steps only) whose copy steps stage a ``bundle.tar`` per step
    (preserving Unix modes) and whose run paths reference s3:/ keys under ami-builds/<build-id>/.
    """
    build_id, bucket, prefix = _stage_ami_build_to_s3(config_file, stack=stack)
    _compact_line_ok(_GROUP_STAGE, f"{build_id}  s3://{bucket}/{prefix}")


@ami_build_group.command("list")
@click.option(
    "--archived",
    is_flag=True,
    help="List archived builds instead of active staged builds.",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "plain"]),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def ami_build_list_staged(
    archived: bool,
    output: str,
    stack: str,
) -> None:
    """List staged AMI builds (active under ami-builds/, or archived under ami-build-archive/)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    base = AMI_BUILD_ARCHIVE_PREFIX if archived else AMI_BUILDS_PREFIX
    session = boto3.Session(region_name=region, profile_name=profile)
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


@ami_build_group.command("cancel")
@click.argument("build_id")
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def ami_build_cancel(build_id: str, stack: str) -> None:
    """Move a staged AMI build from ami-builds/ to ami-build-archive/ in the desk bucket."""
    bid = _normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")
    try:
        _archive_staged_ami_build_prefix(bid, stack=stack)
    except click.ClickException as e:
        if "No objects found" in str(e):
            raise click.ClickException(
                f"No active staged build found for id {bid!r} under {AMI_BUILDS_PREFIX}"
            ) from e
        raise


@ami_group.command("list")
@click.option(
    "--output",
    "-o",
    type=click.Choice(["table", "plain"]),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all owned AMIs, not only desk-created ones.",
)
def ami_list(
    output: str,
    show_all: bool,
) -> None:
    """List AMIs created from desk workstations.

    By default shows only AMIs created with 'desk ami create'. Use --all to show
    all AMIs you own in this region.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    amis = list_amis(region=region, profile=profile, managed_only=not show_all)

    if not amis:
        click.echo("No AMIs found.")
        return

    if output == "plain":
        for a in amis:
            source = a.source_instance or "-"
            click.echo(f"{a.image_id}\t{a.name}\t{a.state}\t{a.creation_date}\t{source}")
        return

    # Table format
    max_id = max(len(a.image_id) for a in amis)
    max_name = max(len(a.name) for a in amis)
    max_state = max(len(a.state) for a in amis)
    max_date = max(len(a.creation_date) for a in amis)
    max_source = max(len(a.source_instance or "-") for a in amis)
    max_id = max(max_id, 9)  # "IMAGE ID"
    max_name = max(max_name, 4)
    max_state = max(max_state, 5)
    max_date = max(max_date, 7)
    max_source = max(max_source, 7)

    header = (
        f"{'IMAGE ID':<{max_id}}  {'NAME':<{max_name}}  "
        f"{'STATE':<{max_state}}  {'CREATED':<{max_date}}  SOURCE"
    )
    click.echo(header)
    click.echo("-" * len(header))

    for a in amis:
        source = a.source_instance or "-"
        click.echo(
            f"{a.image_id:<{max_id}}  {a.name:<{max_name}}  "
            f"{a.state:<{max_state}}  {a.creation_date:<{max_date}}  {source}"
        )


@ami_group.command("create")
@click.argument("workstation", required=True)
@click.option(
    "--name",
    "-n",
    default=None,
    help="Name for the AMI. Default: <workstation-name>-YYYYMMDD-HHMMSS",
)
@click.option(
    "--description",
    "-d",
    default=None,
    help="Description for the AMI.",
)
@click.option(
    "--no-reboot",
    is_flag=True,
    help="Don't reboot the instance before creating the image. May result in inconsistent filesystem.",
)
@click.option(
    "--wait/--no-wait",
    default=True,
    help="Wait for the AMI to become available. Default: wait.",
)
@click.option(
    "--timeout",
    default=1200,
    show_default=True,
    help="Timeout in seconds when waiting for AMI to become available.",
)
def ami_create(
    workstation: str,
    name: str | None,
    description: str | None,
    no_reboot: bool,
    wait: bool,
    timeout: int,
) -> None:
    """Create an AMI from a workstation.

    WORKSTATION can be the instance ID (e.g. i-abc123) or the workstation name.

    The instance will be rebooted during AMI creation unless --no-reboot is specified.
    Using --no-reboot may result in an inconsistent filesystem state in the AMI.

    \b
    Examples:
        desk ami create main
        desk ami create main --name my-custom-ami
        desk ami create i-abc123 --no-reboot --no-wait
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    # Resolve workstation - allow any state except terminated
    try:
        instance_id = resolve_workstation(
            workstation,
            region=region,
            profile=profile,
            states=["pending", "running", "stopping", "stopped"],
        )
    except ValueError as e:
        raise click.UsageError(str(e)) from e

    # Generate default AMI name if not provided
    if not name:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Use the workstation argument as base name (could be name or ID)
        base_name = workstation if not workstation.startswith("i-") else f"workstation-{workstation}"
        name = f"{base_name}-{timestamp}"

    click.echo(f"Creating AMI from {instance_id}...")

    # Check instance state
    state = get_instance_state(instance_id, region=region, profile=profile)
    if state == "stopping":
        click.echo("Instance is stopping, waiting for it to stop...")
        if not wait_for_instance_state(
            instance_id, "stopped", region=region, profile=profile, timeout=300
        ):
            raise click.ClickException("Timed out waiting for instance to stop.")
        state = "stopped"

    if state == "pending":
        click.echo("Instance is starting, waiting for it to run...")
        if not wait_for_instance_state(
            instance_id, "running", region=region, profile=profile, timeout=300
        ):
            raise click.ClickException("Timed out waiting for instance to start.")
        state = "running"

    if no_reboot:
        click.echo("Using --no-reboot: filesystem may be in inconsistent state.")
    elif state == "running":
        click.echo("Instance will be rebooted during AMI creation.")

    # Create the AMI
    image_id = create_ami(
        instance_id=instance_id,
        name=name,
        description=description,
        no_reboot=no_reboot,
        region=region,
        profile=profile,
    )

    click.echo(f"AMI creation started: {image_id}")
    click.echo(f"  Name: {name}")

    if not wait:
        click.echo()
        click.echo("AMI is being created in the background.")
        click.echo(f"Check status: aws ec2 describe-images --image-ids {image_id}")
        return

    # Wait for AMI to become available with progress indicator
    click.echo()
    click.echo("Waiting for AMI to become available...")

    start_time = time.monotonic()
    poll_interval = 10.0
    last_state = None

    while time.monotonic() - start_time < timeout:
        state = get_ami_state(image_id, region=region, profile=profile)
        if state != last_state:
            if state == "pending":
                click.echo("  Status: pending (creating snapshot and registering image)")
            elif state:
                click.echo(f"  Status: {state}")
            last_state = state

        if state == "available":
            elapsed = int(time.monotonic() - start_time)
            click.echo()
            click.secho("AMI created successfully!", fg="green", bold=True)
            click.echo()
            click.echo(f"  AMI ID:  {image_id}")
            click.echo(f"  Name:    {name}")
            click.echo(f"  Time:    {elapsed}s")
            click.echo()
            click.echo("Use this AMI with:")
            click.echo(f"  desk create --ami {image_id}")
            return

        if state in ("failed", "error", "deregistered"):
            raise click.ClickException(f"AMI creation failed with state: {state}")

        time.sleep(poll_interval)

    raise click.ClickException(
        f"Timed out waiting for AMI to become available after {timeout}s. "
        f"AMI {image_id} may still be creating - check AWS console."
    )
