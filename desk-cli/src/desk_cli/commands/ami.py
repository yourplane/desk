"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
import click
from botocore.exceptions import ClientError

from desk.aws import (
    create_ami,
    create_workstation,
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
    wait_for_ssm_ready,
)
from desk_cli import __version__
from desk_cli.commands.copy import shell_command_s3_to_workstation
from desk_cli.commands.run import run_script_on_instance
from desk_cli.commands.scp import scp_transfer
from desk.config import get_desk_settings


AMI_BUILDS_PREFIX = "ami-builds/"
AMI_BUILD_ARCHIVE_PREFIX = "ami-build-archive/"
# Written by `desk ami build step` after the builder instance is launched.
BUILDER_INSTANCE_KEY = "builder-instance.json"
# SSM Run Command Comment prefix to map invocations to recipe steps (≤100 chars; see _ami_build_comment_tag).
AMI_BUILD_COMMENT_PREFIX = "desk-ami-build:"


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


def _builder_name(ami_name: str) -> str:
    """Generate a unique builder instance name from the target AMI name."""
    base = ami_name.lower().strip()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-") or "ami-builder"
    base = base[: 240]  # leave room for "-" + 8 hex chars
    return f"{base}-{secrets.token_hex(4)}"


def _versioned_ami_name(ami_name: str) -> str:
    """Append a timestamp to the AMI name so repeated builds do not collide."""
    # AWS AMI names are limited to 128 characters
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    max_base = 128 - len(timestamp) - 1  # 1 for the hyphen
    base = ami_name.strip()
    if len(base) > max_base:
        base = base[:max_base]
    return f"{base}-{timestamp}"


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


def _workstation_name_for_async_build(build_id: str) -> str:
    """Deterministic workstation Name tag for the async AMI builder instance."""
    bid = _normalize_build_id_arg(build_id)
    base = bid.lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "build"
    base = base[:220]
    return f"ami-build-async-{base}"


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

    return AsyncAmiBuildSnapshot(
        build_id=bid,
        bucket=bucket,
        s3_prefix=prefix,
        config=config,
        recorded_instance_id=recorded_id,
        ec2_state=ec2_state,
        ec2_missing=ec2_missing,
        ssm_ready=ssm_ready,
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


def _wrap_async_builder_shell_needing_aws(inner_command: str) -> str:
    """Stock Ubuntu builder AMIs may not include ``aws``; install ``awscli`` before ``aws s3`` usage."""
    return (
        "set -eu\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "if ! command -v aws >/dev/null 2>&1; then\n"
        "  apt-get update -qq\n"
        "  apt-get install -y awscli\n"
        "fi\n"
        f"{inner_command}\n"
    )


def _async_shell_for_copy_step(
    copy_item: dict[str, Any],
    *,
    bucket: str,
    region: str | None,
) -> str:
    src = copy_item["source"]
    dest = copy_item["dest"]
    recursive = copy_item.get("recursive", False)
    key = _staged_s3_object_key(src)
    inner = shell_command_s3_to_workstation(
        bucket,
        key,
        dest,
        recursive=recursive,
        region=region,
    )
    return _wrap_async_builder_shell_needing_aws(inner)


def _async_shell_for_run_step(
    run_value: str,
    step_index: int,
    *,
    bucket: str,
    region: str | None,
) -> str:
    rv = run_value.strip()
    if rv.startswith("s3:/"):
        key = _staged_s3_object_key(rv)
        region_str = region or "us-east-1"
        tmp = f"/tmp/desk-ami-run-{step_index}.sh"
        inner = (
            f"aws s3 cp s3://{bucket}/{key} {tmp!r} --region {region_str!r} "
            f"&& bash {tmp}"
        )
        return _wrap_async_builder_shell_needing_aws(inner)
    return rv


def _expected_async_shell_for_step(
    step: dict[str, Any],
    step_index: int,
    *,
    bucket: str,
    region: str | None,
) -> str:
    if "run" in step:
        return _async_shell_for_run_step(
            step["run"], step_index, bucket=bucket, region=region
        )
    return _async_shell_for_copy_step(step["copy"], bucket=bucket, region=region)


@dataclass(frozen=True)
class AsyncRecipeEval:
    """Derived from SSM Run Command history for the builder instance."""

    total_steps: int
    steps: tuple[dict[str, Any], ...]
    blocked: bool
    blocked_step_index: int | None
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
        expected = _expected_async_shell_for_step(
            step, i, bucket=bucket, region=region
        )
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
        last_error=None,
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=None,
        recipe_complete=True,
    )


def _print_async_ami_build_status(snap: AsyncAmiBuildSnapshot) -> None:
    """Human-readable status for async AMI build (also used at the start of `step`)."""
    ami_name = snap.config.get("ami_name", "-")
    click.echo(f"AMI build (async): {snap.build_id}")
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
        aws = get_desk_settings().aws_settings
        ev = _evaluate_async_recipe(
            snap.recorded_instance_id,
            build_id=snap.build_id,
            config=snap.config,
            bucket=snap.bucket,
            region=aws.region,
            profile=aws.profile,
        )
        click.echo("  Recipe:")
        click.echo(f"    Steps in config: {ev.total_steps}")
        if ev.recipe_complete:
            click.echo(
                "    State: all steps completed successfully "
                "(AMI create / terminate not run by this tool yet)."
            )
            if ev.total_steps > 0:
                click.echo(f"    Last completed step index: {ev.total_steps - 1}")
        elif ev.blocked:
            click.echo(
                f"    State: failed at step {ev.blocked_step_index} "
                "(run `desk ami build cancel` before staging a new build)."
            )
            if ev.last_error:
                click.echo(f"    Last error: {ev.last_error}")
        elif ev.in_progress_step_index is not None:
            click.echo(
                f"    State: step {ev.in_progress_step_index} in progress "
                f"(SSM command_id={ev.in_progress_command_id!r})."
            )
            if ev.in_progress_step_index > 0:
                click.echo(
                    f"    Last completed step index: {ev.in_progress_step_index - 1}"
                )
        elif ev.next_step_index is not None:
            click.echo(
                f"    State: ready to start step index {ev.next_step_index} "
                "(`desk ami build step`)."
            )
            if ev.next_step_index > 0:
                click.echo(
                    f"    Last completed step index: {ev.next_step_index - 1}"
                )
    elif snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
        click.echo(
            "Waiting for SSM on the builder instance. "
            "Run `desk ami build status` or `step` again later (no long waits in this command)."
        )
    elif snap.ec2_state in ("stopped", "stopping", "shutting-down"):
        click.echo(
            "The builder instance is not in a running state; fix the instance or terminate "
            "and archive the build."
        )


def _run_async_ami_build_step(snap: AsyncAmiBuildSnapshot) -> None:
    """Perform at most one quick action for `desk ami build step` (after status output)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    if snap.recorded_instance_id:
        if snap.ec2_missing or snap.ec2_state == "terminated":
            raise click.ClickException(
                "Refusing to create a new builder instance: this build already recorded "
                f"{snap.recorded_instance_id!r}, and that instance is no longer usable. "
                "Use `desk ami build cancel` or investigate in AWS."
            )
        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is True:
            assert snap.recorded_instance_id is not None
            ev = _evaluate_async_recipe(
                snap.recorded_instance_id,
                build_id=snap.build_id,
                config=snap.config,
                bucket=snap.bucket,
                region=region,
                profile=profile,
            )
            if ev.blocked:
                click.echo()
                click.echo(
                    "Recipe step failed. Run `desk ami build cancel` to archive this build, "
                    "then fix the recipe and stage a new build."
                )
                if ev.last_error:
                    click.echo(f"Last error: {ev.last_error}")
                return
            if ev.in_progress_step_index is not None:
                click.echo()
                click.echo(
                    f"(No step taken: step {ev.in_progress_step_index} is still in progress on SSM.)"
                )
                return
            if ev.recipe_complete:
                click.echo()
                click.secho(
                    "Recipe finished (AMI create / terminate is out of scope for this command).",
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
            click.echo()
            click.echo(
                f"Started recipe step {ev.next_step_index} ({kind}): SSM command_id={command_id}"
            )
            click.secho(
                "Step initiated (not waiting for completion). Check `desk ami build status`.",
                fg="green",
            )
            return
        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
            click.echo()
            click.echo("(No step taken: waiting for SSM.)")
            return
        if snap.ec2_state in ("stopped", "stopping", "shutting-down"):
            click.echo()
            click.echo("(No step taken: instance not running.)")
            return
        raise click.ClickException(
            f"Unexpected builder state (ec2_state={snap.ec2_state!r}, "
            f"ec2_missing={snap.ec2_missing})."
        )

    instance_type = snap.config.get("instance_type", "t3.medium")
    workstation_name = _workstation_name_for_async_build(snap.build_id)
    builder_ami = get_latest_ubuntu_ami(region=region, profile=profile)
    click.echo()
    click.echo(f"Creating builder instance {workstation_name!r}...")
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
    click.echo(f"Recorded {instance_id} in s3://{snap.bucket}/{key}")
    click.secho("Step complete: builder instance created and id written to S3.", fg="green")


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
def ami_build_status(build_id: str, stack: str) -> None:
    """Show async AMI build progress from S3, EC2, and SSM Run Command history (quick; does not wait).

    Recipe progress is derived from SSM commands on the builder instance (Comment tag and/or
    command body match). After a step fails, archive with `desk ami build cancel` before
    staging a new build.
    """
    snap = _resolve_async_ami_build_snapshot(build_id, stack=stack)
    _print_async_ami_build_status(snap)


@ami_build_group.command("step")
@click.argument("build_id")
@click.option(
    "--stack",
    default="desk",
    show_default=True,
    help="CloudFormation stack name for desk (used to resolve S3 bucket).",
)
def ami_build_step(build_id: str, stack: str) -> None:
    """Advance the async AMI build by one quick action, or no-op if there is nothing to do.

    Prints the same summary as `desk ami build status`, then creates the builder instance
    (recording its id in S3) when needed. When SSM is ready, starts at most one recipe
    ``run``/``copy`` step via SSM and returns immediately after ``SendCommand`` (does not
    wait for the remote command). Skips if a prior step failed (use ``cancel``) or a step
    is still in progress on SSM. Final AMI registration is not performed here.
    """
    snap = _resolve_async_ami_build_snapshot(build_id, stack=stack)
    _print_async_ami_build_status(snap)
    _run_async_ami_build_step(snap)


@ami_build_group.command("run")
@click.argument(
    "config_file",
    type=click.Path(exists=True),
)
@click.option(
    "--no-wait",
    is_flag=True,
    help="Do not wait for AMI to become available after creation.",
)
def ami_build_run(
    config_file: str,
    no_wait: bool,
) -> None:
    """Build an AMI from a config file: create instance, copy files, run scripts, then ami create.

    CONFIG_FILE is a JSON file with:
      instance_type (optional): e.g. t3.medium (default: t3.medium).
      The builder always starts from the latest Ubuntu 24.04 LTS AMI (config default/ami_prefix is not used).
      steps: list of steps in order; each step is {\"run\": \"cmd\"} or {\"copy\": {\"source\": \"...\", \"dest\": \"...\", \"recursive\": optional}}.
      Alternatively (legacy) copy + run + optional run_before_copy.
      ami_name: base name for the created AMI (a timestamp is appended so reruns do not collide)

    To keep the builder home directory clean, configure the recipe to copy into a staging
    directory (e.g. /tmp/desk-build), run install scripts from there, and install only final
    deliverables into home or system paths.

    The builder instance name is auto-generated from ami_name plus random characters so multiple
    builds can run in parallel. The final AMI name is ami_name with a -YYYYMMDD-HHMMSS suffix so
    you can rerun the same recipe without duplicate-name errors. On success the builder is
    terminated; on failure it is left running for debugging.
    """
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    config = _load_build_config(config_file)
    _validate_build_recipe_config(config, config_file)
    instance_type = config.get("instance_type", "t3.medium")
    steps = _get_build_steps(config)
    ami_name = config.get("ami_name")
    assert ami_name  # validated above
    workstation_name = _builder_name(ami_name)

    click.echo(f"Building AMI from config: {config_file}")
    click.echo(f"  Workstation: {workstation_name}")
    click.echo()

    # 1. Create instance from Ubuntu (never use config default/ami_prefix for builder)
    builder_ami = get_latest_ubuntu_ami(region=region, profile=profile)
    click.echo("Step 1/4: Creating builder instance...")
    try:
        create_workstation(
            workstation_name,
            instance_type,
            ami_id=builder_ami,
            shutdown_after="4h",
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    # 2. Wait for SSM
    click.echo("Step 2/4: Waiting for instance to be ready (SSM)...")
    try:
        instance_id = resolve_workstation(
            workstation_name,
            region=region,
            profile=profile,
            states=["pending", "running"],
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if not wait_for_ssm_ready(
        instance_id, region=region, profile=profile, timeout=600
    ):
        raise click.ClickException(
            f"Instance {instance_id} did not become SSM-ready within 600s."
        )
    click.echo("  Instance ready.")
    click.echo()

    # 3. Copy files and run scripts (steps can be intermixed)
    config_dir = os.path.dirname(os.path.abspath(config_file))

    for i, step in enumerate(steps):
        if "run" in step:
            cmd = step["run"]
            run_cmd = cmd
            if not os.path.isabs(cmd) and ("/" in cmd or cmd.endswith(".sh")):
                candidate = os.path.normpath(os.path.join(config_dir, cmd))
                if os.path.isfile(candidate):
                    run_cmd = candidate
            click.echo(f"Step 3/4: Run ({i + 1}/{len(steps)}): {run_cmd}")
            script_content = run_cmd
            if os.path.isfile(run_cmd):
                with open(run_cmd) as f:
                    script_content = f.read()
            run_script_on_instance(
                instance_id,
                script_content,
                follow=True,
                region=region,
                profile=profile,
                command_timeout=7200,
            )
        else:
            item = step["copy"]
            src = item["source"]
            dest = item["dest"]
            recursive = item.get("recursive", False)
            if not os.path.isabs(src):
                src = os.path.normpath(os.path.join(config_dir, src))
            click.echo(f"Step 3/4: Copy ({i + 1}/{len(steps)}): {src} -> {workstation_name}:{dest}")
            scp_transfer(
                workstation_name,
                src,
                f"{workstation_name}:{dest}",
                user="ubuntu",
                identity_file=None,
                wait=False,
                wait_timeout=300,
                recursive=recursive,
                region=region,
                profile=profile,
                replace_process=False,
            )

    click.echo()

    # 4. Create AMI
    versioned_name = _versioned_ami_name(ami_name)
    click.echo("Step 4/4: Creating AMI from builder...")
    click.echo(f"  AMI name: {versioned_name}")
    image_id = create_ami(
        instance_id=instance_id,
        name=versioned_name,
        description=None,
        no_reboot=False,
        region=region,
        profile=profile,
    )
    click.echo(f"  AMI ID: {image_id}")
    if not no_wait:
        click.echo("  Waiting for AMI to become available...")
        if not wait_for_ami_available(
            image_id,
            region=region,
            profile=profile,
            timeout=1200,
        ):
            raise click.ClickException(
                f"AMI {image_id} did not become available within timeout."
            )

    click.echo()
    click.echo("Terminating builder instance...")
    terminate_instance(instance_id, region=region, profile=profile)
    click.secho("AMI build complete.", fg="green", bold=True)


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

    Writes a normalized config (steps only) whose copy/run paths reference s3:/ keys under
    ami-builds/<build-id>/.
    """
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
                base_prefix = f"{prefix}files/copy/{i}/"
                for root, _dirs, files in os.walk(resolved):
                    for f in files:
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, resolved).replace(os.sep, "/")
                        key = f"{base_prefix}{rel}"
                        s3.upload_file(full, bucket, key)
                item["source"] = _s3_uri_for_key(base_prefix.rstrip("/") + "/")
            else:
                rel = _artifact_rel_path(resolved, config_dir)
                key = f"{prefix}files/copy/{i}/{rel}"
                s3.upload_file(resolved, bucket, key)
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
        ContentType="application/json",
    )

    click.echo(f"Staged AMI build {build_id}")
    click.echo(f"  s3:/{prefix}")
    click.echo(f"  Bucket: s3://{bucket}/{prefix}")


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
            f"No active staged build found for id {bid!r} under {AMI_BUILDS_PREFIX}"
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

    click.echo(f"Archived AMI build {bid} to s3:/{dest_prefix}")


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
