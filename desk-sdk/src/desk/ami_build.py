"""AMI build snapshot, status, list, and archive helpers (shared by CLI and API)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError

from desk.aws import (
    generate_presigned_get_object_url,
    get_ami_state,
    get_command_invocation,
    get_desk_copy_bucket,
    get_instance_state,
    get_ssm_command,
    is_ssm_ready,
    list_command_invocations_for_instance,
)

AMI_BUILDS_PREFIX = "ami-builds/"
AMI_BUILD_ARCHIVE_PREFIX = "ami-build-archive/"
BUILDER_INSTANCE_KEY = "builder-instance.json"
TEST_INSTANCE_KEY = "test-instance.json"
AMI_RESULT_KEY = "ami-result.json"
AMI_BUILD_COMMENT_PREFIX = "desk-ami-build:"
AMI_TEST_COMMENT_PREFIX = "desk-ami-test:"
AMI_COPY_BUNDLE_NAME = "bundle.tar"


class AmiBuildError(Exception):
    """Base error for AMI build operations."""


class AmiBuildNotFoundError(AmiBuildError):
    """Staged build prefix or config is missing."""


@dataclass(frozen=True)
class AmiBuildSnapshot:
    """AWS-derived state for an AMI build (active or archived)."""

    build_id: str
    bucket: str
    s3_prefix: str
    archived: bool
    config: dict[str, Any]
    manifest: dict[str, Any] | None
    recorded_instance_id: str | None
    ec2_state: str | None
    ec2_missing: bool
    ssm_ready: bool | None
    ami_result: dict[str, Any] | None
    registered_ami_id: str | None
    registered_ami_state: str | None
    async_pipeline_fully_complete: bool
    test_recorded_instance_id: str | None
    test_ec2_state: str | None
    test_ec2_missing: bool
    test_ssm_ready: bool | None


@dataclass(frozen=True)
class RecipeEval:
    """Derived from SSM Run Command history for a builder or test instance."""

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


def normalize_build_id(arg: str) -> str:
    s = arg.strip().strip("/")
    for prefix in (AMI_BUILDS_PREFIX, AMI_BUILD_ARCHIVE_PREFIX):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.rstrip("/")


def get_build_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    if "build" in config and config.get("build") is not None:
        return list(config["build"])
    steps = config.get("steps")
    if steps is not None:
        return list(steps)
    out: list[dict[str, Any]] = []
    for cmd in config.get("run_before_copy") or []:
        out.append({"run": cmd})
    for item in config.get("copy") or []:
        out.append({"copy": item})
    for cmd in config.get("run") or []:
        out.append({"run": cmd})
    return out


def get_test_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
    t = config.get("test")
    if t is None:
        return []
    return list(t)


def _truncate_status_text(s: str, max_len: int = 100) -> str:
    t = " ".join(s.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def describe_recipe_step(step: dict[str, Any]) -> str:
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
    return f"copy{extra}: {_truncate_status_text(src)} -> {_truncate_status_text(dst)}"


def _read_s3_object_json(s3: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            return None
        raise
    raw = json.loads(obj["Body"].read())
    if not isinstance(raw, dict):
        raise AmiBuildError(f"S3 object {key} must be a JSON object.")
    return raw


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


def _ami_image_id_from_result(ami_result: dict[str, Any] | None) -> str | None:
    if not ami_result:
        return None
    raw = ami_result.get("image_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def needs_post_builder_test_work(snap: AmiBuildSnapshot) -> bool:
    if snap.async_pipeline_fully_complete:
        return False
    if snap.ami_result and snap.ami_result.get("test_failed"):
        return False
    if not get_test_steps(snap.config):
        return False
    if not snap.registered_ami_id or snap.registered_ami_state != "available":
        return False
    return True


def resolve_ami_build_snapshot(
    build_id: str,
    *,
    stack: str = "desk",
    archived: bool = False,
    region: str | None = None,
    profile: str | None = None,
) -> AmiBuildSnapshot:
    bid = normalize_build_id(build_id)
    if not bid:
        raise AmiBuildError("Build id is empty.")

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise AmiBuildError(str(e)) from e

    base = AMI_BUILD_ARCHIVE_PREFIX if archived else AMI_BUILDS_PREFIX
    prefix = f"{base}{bid}/"
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")

    config = _read_s3_object_json(s3, bucket, f"{prefix}config.json")
    if config is None:
        location = "archive" if archived else "active builds"
        raise AmiBuildNotFoundError(
            f"No staged AMI build found for id {bid!r} in {location} "
            f"(missing {prefix}config.json)."
        )

    manifest = _read_s3_object_json(s3, bucket, f"{prefix}manifest.json")

    builder_doc = _read_s3_object_json(s3, bucket, f"{prefix}{BUILDER_INSTANCE_KEY}")
    recorded_id: str | None = None
    if builder_doc is not None:
        iid = builder_doc.get("instance_id")
        if isinstance(iid, str) and iid.strip():
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

    ami_result = _read_s3_object_json(s3, bucket, f"{prefix}{AMI_RESULT_KEY}")

    test_doc = _read_s3_object_json(s3, bucket, f"{prefix}{TEST_INSTANCE_KEY}")
    test_recorded_id: str | None = None
    if test_doc is not None:
        tid = test_doc.get("instance_id")
        if isinstance(tid, str) and tid.strip():
            test_recorded_id = tid.strip()

    test_ec2_missing = False
    test_ec2_state: str | None = None
    test_ssm_ready: bool | None = None
    if test_recorded_id:
        test_ec2_state = _safe_get_instance_state(
            test_recorded_id, region=region, profile=profile
        )
        if test_ec2_state is None:
            test_ec2_missing = True
        elif test_ec2_state in ("running", "pending"):
            test_ssm_ready = is_ssm_ready(test_recorded_id, region=region, profile=profile)
        elif test_ec2_state in ("stopped", "stopping", "shutting-down"):
            test_ssm_ready = False
        elif test_ec2_state == "terminated":
            test_ssm_ready = None

    reg_image_id = _ami_image_id_from_result(ami_result)
    reg_ami_state: str | None = None
    if reg_image_id:
        reg_ami_state = get_ami_state(reg_image_id, region=region, profile=profile)

    has_test_recipe = len(get_test_steps(config)) > 0
    pipeline_done = False
    if ami_result and ami_result.get("pipeline_complete") is True and reg_image_id:
        pipeline_done = True
    elif (
        not has_test_recipe
        and reg_image_id
        and reg_ami_state == "available"
        and (ec2_state == "terminated" or ec2_missing)
    ):
        pipeline_done = True

    return AmiBuildSnapshot(
        build_id=bid,
        bucket=bucket,
        s3_prefix=prefix,
        archived=archived,
        config=config,
        manifest=manifest,
        recorded_instance_id=recorded_id,
        ec2_state=ec2_state,
        ec2_missing=ec2_missing,
        ssm_ready=ssm_ready,
        ami_result=ami_result,
        registered_ami_id=reg_image_id,
        registered_ami_state=reg_ami_state,
        async_pipeline_fully_complete=pipeline_done,
        test_recorded_instance_id=test_recorded_id,
        test_ec2_state=test_ec2_state,
        test_ec2_missing=test_ec2_missing,
        test_ssm_ready=test_ssm_ready,
    )


def _ami_recipe_comment_tag(build_id: str, step_index: int, kind: str, prefix: str) -> str:
    bid = normalize_build_id(build_id)
    base = f"{prefix}{bid}:{step_index}:{kind}"
    if len(base) <= 100:
        return base
    short = hashlib.sha256(bid.encode()).hexdigest()[:12]
    return f"{prefix}{short}:{step_index}:{kind}"


def ami_build_comment_tag(build_id: str, step_index: int, kind: str) -> str:
    return _ami_recipe_comment_tag(build_id, step_index, kind, AMI_BUILD_COMMENT_PREFIX)


def ami_test_comment_tag(build_id: str, step_index: int, kind: str) -> str:
    return _ami_recipe_comment_tag(build_id, step_index, kind, AMI_TEST_COMMENT_PREFIX)


def _parse_ami_recipe_comment(
    comment: str | None, build_id: str, prefix: str
) -> tuple[int, str] | None:
    if not comment or not comment.startswith(prefix):
        return None
    bid = normalize_build_id(build_id)
    rest = comment[len(prefix) :]
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
        raise AmiBuildError(
            f"Staged copy source must be s3:/… (got {src!r}). Re-run `desk ami build create`."
        )
    return s[4:].lstrip("/")


def _tar_member_name_for_single_file(source: str, dest: str) -> str:
    if dest.endswith("/") or dest.endswith(os.sep):
        return os.path.basename(source)
    d = dest.rstrip("/")
    base = os.path.basename(d)
    if not base:
        return os.path.basename(source)
    return base


def _parent_dir_for_file_copy_dest(dest: str) -> str:
    if dest.endswith("/") or dest.endswith(os.sep):
        return dest.rstrip("/") or "."
    d = os.path.dirname(dest)
    return d if d else "."


def _async_shell_for_copy_step(
    copy_item: dict[str, Any],
    *,
    bucket: str,
    region: str | None,
    profile: str | None,
) -> str:
    src = copy_item["source"]
    raw_dest = copy_item["dest"]
    recursive = copy_item.get("recursive", False)
    key = _staged_s3_object_key(src)
    url = generate_presigned_get_object_url(bucket, key, region=region, profile=profile)
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
        url = generate_presigned_get_object_url(bucket, key, region=region, profile=profile)
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
        return _async_shell_for_run_step(
            step["run"], step_index, bucket=bucket, region=region, profile=profile
        )
    return _async_shell_for_copy_step(
        step["copy"], bucket=bucket, region=region, profile=profile
    )


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


def _map_invocation_to_step_index(
    command_id: str,
    *,
    build_id: str,
    steps: list[dict[str, Any]],
    bucket: str,
    region: str | None,
    profile: str | None,
    comment_prefix: str,
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
    parsed = _parse_ami_recipe_comment(cmd_doc.get("Comment"), build_id, comment_prefix)
    if parsed is not None:
        return parsed[0]
    norm = _normalize_shell_for_compare(shell)
    for i, step in enumerate(steps):
        try:
            expected = expected_async_shell_for_step(
                step, i, bucket=bucket, region=region, profile=profile
            )
        except AmiBuildError:
            continue
        if norm == _normalize_shell_for_compare(expected):
            return i
    return None


def evaluate_recipe(
    instance_id: str,
    *,
    build_id: str,
    steps: list[dict[str, Any]],
    bucket: str,
    region: str | None,
    profile: str | None,
    comment_prefix: str,
) -> RecipeEval:
    n = len(steps)
    if n == 0:
        return RecipeEval(
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
            comment_prefix=comment_prefix,
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
                inv = get_command_invocation(cid, instance_id, region=region, profile=profile)
                exit_code = inv.exit_code
                stderr = inv.stderr or ""
            except ClientError:
                pass
        return st, exit_code, stderr

    for i in range(n):
        row = by_step.get(i)
        if row is None:
            return RecipeEval(
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
            return RecipeEval(
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
            return RecipeEval(
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
            return RecipeEval(
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

    return RecipeEval(
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


def evaluate_build_recipe(
    snap: AmiBuildSnapshot,
    *,
    region: str | None = None,
    profile: str | None = None,
) -> RecipeEval | None:
    if not snap.recorded_instance_id:
        return None
    if snap.ec2_missing or snap.ec2_state == "terminated":
        return None
    if snap.ec2_state not in ("running", "pending"):
        return None
    if snap.ssm_ready is not True:
        return None
    return evaluate_recipe(
        snap.recorded_instance_id,
        build_id=snap.build_id,
        steps=get_build_steps(snap.config),
        bucket=snap.bucket,
        region=region,
        profile=profile,
        comment_prefix=AMI_BUILD_COMMENT_PREFIX,
    )


def evaluate_test_recipe(
    snap: AmiBuildSnapshot,
    *,
    region: str | None = None,
    profile: str | None = None,
) -> RecipeEval | None:
    if not snap.test_recorded_instance_id:
        return None
    if snap.test_ec2_missing or snap.test_ec2_state == "terminated":
        return None
    if snap.test_ec2_state not in ("running", "pending"):
        return None
    if snap.test_ssm_ready is not True:
        return None
    steps = get_test_steps(snap.config)
    if not steps:
        return None
    return evaluate_recipe(
        snap.test_recorded_instance_id,
        build_id=snap.build_id,
        steps=steps,
        bucket=snap.bucket,
        region=region,
        profile=profile,
        comment_prefix=AMI_TEST_COMMENT_PREFIX,
    )


def _recipe_eval_to_dict(
    ev: RecipeEval,
    steps: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": label,
        "total_steps": ev.total_steps,
        "recipe_complete": ev.recipe_complete,
        "blocked": ev.blocked,
        "blocked_step_index": ev.blocked_step_index,
        "blocked_command_id": ev.blocked_command_id,
        "last_error": ev.last_error,
        "in_progress_step_index": ev.in_progress_step_index,
        "in_progress_command_id": ev.in_progress_command_id,
        "next_step_index": ev.next_step_index,
        "steps": [
            {"index": i, "description": describe_recipe_step(step)}
            for i, step in enumerate(steps)
        ],
    }
    if ev.blocked and ev.blocked_step_index is not None:
        out["blocked_step_description"] = describe_recipe_step(steps[ev.blocked_step_index])
    if ev.in_progress_step_index is not None:
        out["in_progress_step_description"] = describe_recipe_step(
            steps[ev.in_progress_step_index]
        )
    if ev.next_step_index is not None:
        out["next_step_description"] = describe_recipe_step(steps[ev.next_step_index])
    return out


def _ssm_verbose_output(
    instance_id: str,
    command_id: str | None,
    *,
    region: str | None,
    profile: str | None,
) -> dict[str, Any] | None:
    if not command_id:
        return None
    try:
        doc = get_ssm_command(command_id, region=region, profile=profile)
        params = doc.get("Parameters") or {}
        cmds = params.get("commands")
        script = str(cmds[0]) if isinstance(cmds, list) and cmds else ""
        inv = get_command_invocation(command_id, instance_id, region=region, profile=profile)
        return {
            "command_id": command_id,
            "script": script,
            "stdout": inv.stdout or "",
            "stderr": inv.stderr or "",
            "status": inv.status,
            "exit_code": inv.exit_code,
        }
    except (ClientError, RuntimeError, OSError) as e:
        return {"command_id": command_id, "error": str(e)}


def status_summary(
    snap: AmiBuildSnapshot,
    *,
    recipe_eval: RecipeEval | None = None,
    test_eval: RecipeEval | None = None,
    region: str | None = None,
    profile: str | None = None,
) -> dict[str, str]:
    """Short per-row status for list views."""
    if snap.async_pipeline_fully_complete:
        return {"phase": "complete", "label": "Complete"}

    if snap.ami_result and snap.ami_result.get("test_failed"):
        return {"phase": "failed", "label": "Test phase failed"}

    if recipe_eval is None:
        recipe_eval = evaluate_build_recipe(snap, region=region, profile=profile)
    if test_eval is None:
        test_eval = evaluate_test_recipe(snap, region=region, profile=profile)

    if recipe_eval and recipe_eval.blocked:
        idx = recipe_eval.blocked_step_index
        return {
            "phase": "failed",
            "label": f"Build failed at step {idx}" if idx is not None else "Build failed",
        }
    if test_eval and test_eval.blocked:
        idx = test_eval.blocked_step_index
        return {
            "phase": "failed",
            "label": f"Test failed at step {idx}" if idx is not None else "Test failed",
        }

    if recipe_eval and recipe_eval.in_progress_step_index is not None:
        i = recipe_eval.in_progress_step_index
        total = recipe_eval.total_steps
        return {"phase": "build", "label": f"Build step {i + 1}/{total} in progress"}

    if not snap.recorded_instance_id:
        return {"phase": "pending", "label": "Awaiting builder instance"}

    if snap.recorded_instance_id and snap.ec2_state in ("running", "pending"):
        if snap.ssm_ready is False:
            return {"phase": "build", "label": "Waiting for SSM on builder"}
        if recipe_eval and recipe_eval.next_step_index is not None:
            i = recipe_eval.next_step_index
            total = recipe_eval.total_steps
            return {"phase": "build", "label": f"Build step {i + 1}/{total} pending"}

    if recipe_eval and recipe_eval.recipe_complete:
        if snap.registered_ami_id:
            st = snap.registered_ami_state or "unknown"
            if st == "available":
                if test_eval and test_eval.in_progress_step_index is not None:
                    i = test_eval.in_progress_step_index
                    total = test_eval.total_steps
                    return {"phase": "test", "label": f"Test step {i + 1}/{total} in progress"}
                if test_eval and test_eval.next_step_index is not None:
                    i = test_eval.next_step_index
                    total = test_eval.total_steps
                    return {"phase": "test", "label": f"Test step {i + 1}/{total} pending"}
                if get_test_steps(snap.config) and not snap.test_recorded_instance_id:
                    return {"phase": "test", "label": "Awaiting test instance"}
                if needs_post_builder_test_work(snap):
                    return {"phase": "test", "label": "Test phase pending"}
            return {"phase": "ami", "label": f"AMI {st}"}
        return {"phase": "ami", "label": "AMI registration pending"}

    if needs_post_builder_test_work(snap) or snap.test_recorded_instance_id:
        if test_eval and test_eval.in_progress_step_index is not None:
            i = test_eval.in_progress_step_index
            total = test_eval.total_steps
            return {"phase": "test", "label": f"Test step {i + 1}/{total} in progress"}
        return {"phase": "test", "label": "Test phase"}

    return {"phase": "build", "label": "In progress"}


def status_detail(
    snap: AmiBuildSnapshot,
    *,
    verbose: bool = False,
    recipe_eval: RecipeEval | None = None,
    test_eval: RecipeEval | None = None,
    region: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Full pipeline status for detail views."""
    if recipe_eval is None:
        recipe_eval = evaluate_build_recipe(snap, region=region, profile=profile)
    if test_eval is None:
        test_eval = evaluate_test_recipe(snap, region=region, profile=profile)

    manifest = snap.manifest or {}
    summary = status_summary(
        snap, recipe_eval=recipe_eval, test_eval=test_eval, region=region, profile=profile
    )

    detail: dict[str, Any] = {
        "build_id": snap.build_id,
        "ami_name": snap.config.get("ami_name") or manifest.get("ami_name") or "-",
        "created_at": manifest.get("created_at"),
        "archived": snap.archived,
        "bucket": snap.bucket,
        "s3_prefix": snap.s3_prefix,
        "status_summary": summary,
        "pipeline_complete": snap.async_pipeline_fully_complete,
        "builder": {
            "instance_id": snap.recorded_instance_id,
            "ec2_state": snap.ec2_state,
            "ec2_missing": snap.ec2_missing,
            "ssm_ready": snap.ssm_ready,
        },
        "registered_ami": {
            "image_id": snap.registered_ami_id,
            "state": snap.registered_ami_state,
        },
        "test_instance": {
            "instance_id": snap.test_recorded_instance_id,
            "ec2_state": snap.test_ec2_state,
            "ec2_missing": snap.test_ec2_missing,
            "ssm_ready": snap.test_ssm_ready,
        },
        "test_failed": bool(snap.ami_result and snap.ami_result.get("test_failed")),
    }

    build_steps = get_build_steps(snap.config)
    if recipe_eval is not None and snap.recorded_instance_id:
        build_recipe = _recipe_eval_to_dict(recipe_eval, build_steps, label="build")
        if verbose and snap.recorded_instance_id:
            cid = None
            if recipe_eval.blocked and recipe_eval.blocked_command_id:
                cid = recipe_eval.blocked_command_id
            elif recipe_eval.in_progress_command_id:
                cid = recipe_eval.in_progress_command_id
            verbose_out = _ssm_verbose_output(
                snap.recorded_instance_id, cid, region=region, profile=profile
            )
            if verbose_out:
                build_recipe["verbose"] = verbose_out
        detail["build_recipe"] = build_recipe
    elif not snap.recorded_instance_id:
        detail["build_recipe"] = {
            "label": "build",
            "message": "Builder instance not created yet.",
        }

    test_steps = get_test_steps(snap.config)
    if test_steps:
        if test_eval is not None and snap.test_recorded_instance_id:
            test_recipe = _recipe_eval_to_dict(test_eval, test_steps, label="test")
            if verbose:
                cid = None
                if test_eval.blocked and test_eval.blocked_command_id:
                    cid = test_eval.blocked_command_id
                elif test_eval.in_progress_command_id:
                    cid = test_eval.in_progress_command_id
                verbose_out = _ssm_verbose_output(
                    snap.test_recorded_instance_id, cid, region=region, profile=profile
                )
                if verbose_out:
                    test_recipe["verbose"] = verbose_out
            detail["test_recipe"] = test_recipe
        else:
            detail["test_recipe"] = {
                "label": "test",
                "total_steps": len(test_steps),
                "message": "Test instance not ready yet.",
            }

    if recipe_eval and recipe_eval.recipe_complete:
        if snap.async_pipeline_fully_complete and snap.registered_ami_id:
            detail["post_build"] = {
                "message": f"Pipeline complete (registered {snap.registered_ami_id})."
            }
        elif not snap.registered_ami_id:
            detail["post_build"] = {"message": "AMI registration pending."}
        else:
            detail["post_build"] = {
                "image_id": snap.registered_ami_id,
                "ami_state": snap.registered_ami_state,
            }

    return detail


def _list_build_ids(
    bucket: str,
    base_prefix: str,
    *,
    region: str | None,
    profile: str | None,
) -> list[str]:
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    ids: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=base_prefix, Delimiter="/"):
        for p in page.get("CommonPrefixes") or []:
            pref = p.get("Prefix") or ""
            bid = pref[len(base_prefix) :].rstrip("/")
            if bid:
                ids.append(bid)
    ids.sort(reverse=True)
    return ids


def list_ami_builds(
    *,
    archived: bool = False,
    page: int = 1,
    page_size: int = 20,
    stack: str = "desk",
    region: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    if page < 1:
        raise AmiBuildError("page must be >= 1")
    if page_size < 1:
        raise AmiBuildError("page_size must be >= 1")

    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise AmiBuildError(str(e)) from e

    base = AMI_BUILD_ARCHIVE_PREFIX if archived else AMI_BUILDS_PREFIX
    all_ids = _list_build_ids(bucket, base, region=region, profile=profile)
    total = len(all_ids)
    total_pages = max(1, math.ceil(total / page_size)) if total else 0
    start = (page - 1) * page_size
    page_ids = all_ids[start : start + page_size]

    return {
        "items": _build_list_items(
            page_ids, bucket, base, stack, archived, region, profile
        ),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages if total else 0,
    }


def _build_list_items(
    page_ids: list[str],
    bucket: str,
    base: str,
    stack: str,
    archived: bool,
    region: str | None,
    profile: str | None,
) -> list[dict[str, Any]]:
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    items: list[dict[str, Any]] = []
    for bid in page_ids:
        prefix = f"{base}{bid}/"
        manifest = _read_s3_object_json(s3, bucket, f"{prefix}manifest.json") or {}
        ami_name = manifest.get("ami_name", "-")
        try:
            snap = resolve_ami_build_snapshot(
                bid, stack=stack, archived=archived, region=region, profile=profile
            )
            ami_name = snap.config.get("ami_name") or ami_name
            recipe_eval = evaluate_build_recipe(snap, region=region, profile=profile)
            test_eval = evaluate_test_recipe(snap, region=region, profile=profile)
            summary = status_summary(
                snap,
                recipe_eval=recipe_eval,
                test_eval=test_eval,
                region=region,
                profile=profile,
            )
        except AmiBuildNotFoundError:
            summary = {"phase": "unknown", "label": "Config missing"}
        items.append(
            {
                "build_id": bid,
                "ami_name": ami_name,
                "created_at": manifest.get("created_at"),
                "status_summary": summary,
            }
        )
    return items


def _move_s3_prefix_within_bucket(
    bucket: str,
    src_prefix: str,
    dest_prefix: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    pages = list(paginator.paginate(Bucket=bucket, Prefix=src_prefix))
    keys: list[str] = []
    for page in pages:
        for obj in page.get("Contents") or []:
            keys.append(obj["Key"])
    if not keys:
        raise AmiBuildNotFoundError(f"No objects found under s3://{bucket}/{src_prefix}")
    for key in keys:
        suffix = key[len(src_prefix) :]
        new_key = f"{dest_prefix}{suffix}"
        s3.copy_object(
            Bucket=bucket,
            Key=new_key,
            CopySource={"Bucket": bucket, "Key": key},
        )
        s3.delete_object(Bucket=bucket, Key=key)


def archive_ami_build(
    build_id: str,
    *,
    stack: str = "desk",
    region: str | None = None,
    profile: str | None = None,
) -> None:
    """Move ami-builds/<id>/ to ami-build-archive/<id>/ (S3 only; does not terminate EC2)."""
    bid = normalize_build_id(build_id)
    if not bid:
        raise AmiBuildError("Build id is empty.")
    try:
        bucket = get_desk_copy_bucket(stack_name=stack, region=region, profile=profile)
    except RuntimeError as e:
        raise AmiBuildError(str(e)) from e
    src_prefix = f"{AMI_BUILDS_PREFIX}{bid}/"
    dest_prefix = f"{AMI_BUILD_ARCHIVE_PREFIX}{bid}/"
    try:
        _move_s3_prefix_within_bucket(
            bucket, src_prefix, dest_prefix, region=region, profile=profile
        )
    except AmiBuildNotFoundError as e:
        raise AmiBuildNotFoundError(
            f"No active staged build found for id {bid!r} under {AMI_BUILDS_PREFIX}"
        ) from e
