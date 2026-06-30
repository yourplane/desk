"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Optional

import boto3
import click
from botocore.exceptions import ClientError

from desk.aws import (
    AMI_BUILD_STATUS_TESTED,
    AMI_BUILD_STATUS_UNTESTED,
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
    tag_ami_build_status,
    terminate_instance,
    wait_for_ami_available,
    wait_for_instance_state,
)
from desk_cli import __version__
from desk.config import get_desk_settings
from desk.ami_build import (
    AMI_BUILDS_PREFIX,
    AMI_BUILD_ARCHIVE_PREFIX,
    BUILDER_INSTANCE_KEY,
    TEST_INSTANCE_KEY,
    AMI_RESULT_KEY,
    AMI_BUILD_COMMENT_PREFIX,
    AMI_TEST_COMMENT_PREFIX,
    AMI_COPY_BUNDLE_NAME,
    AmiBuildError,
    AmiBuildNotFoundError,
    AmiBuildSnapshot as AsyncAmiBuildSnapshot,
    RecipeEval as AsyncRecipeEval,
    ami_build_comment_tag as _ami_build_comment_tag,
    ami_test_comment_tag as _ami_test_comment_tag,
    archive_ami_build,
    describe_recipe_step as _describe_recipe_step_for_status,
    evaluate_build_recipe as _maybe_evaluate_async_recipe,
    evaluate_test_recipe as _maybe_evaluate_async_test_recipe,
    expected_async_shell_for_step as _expected_async_shell_for_step,
    get_build_steps as _get_build_steps,
    get_test_steps as _get_test_steps,
    needs_post_builder_test_work as _needs_post_builder_test_work,
    normalize_build_id as _normalize_build_id_arg,
    resolve_ami_build_snapshot,
)


@click.group("ami")
def ami_group() -> None:
    """Manage AMIs from desk workstations."""
    pass


def _validate_recipe_step_list(steps: Any, label: str) -> None:
    if not isinstance(steps, list):
        raise click.ClickException(f"Config {label!r} must be a list.")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise click.ClickException(
                f"Config {label}[{i}] must be an object (with 'run' or 'copy')."
            )
        if "run" in step and "copy" in step:
            raise click.ClickException(
                f"Config {label}[{i}] must have either 'run' or 'copy', not both."
            )
        if "run" in step:
            if not isinstance(step["run"], str):
                raise click.ClickException(f"Config {label}[{i}].run must be a string.")
        elif "copy" in step:
            c = step["copy"]
            if not isinstance(c, dict) or "source" not in c or "dest" not in c:
                raise click.ClickException(
                    f"Config {label}[{i}].copy must be an object with 'source' and 'dest'."
                )
        else:
            raise click.ClickException(
                f"Config {label}[{i}] must have 'run' or 'copy'."
            )


def _load_build_config(path: str) -> dict[str, Any]:
    """Load and validate ami build config from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise click.ClickException("Config must be a JSON object.")

    has_build = "build" in data and data.get("build") is not None
    has_steps = "steps" in data and data.get("steps") is not None
    if has_build and has_steps:
        raise click.ClickException("Config must not specify both 'build' and 'steps'; use 'build' only.")

    if has_build:
        _validate_recipe_step_list(data["build"], "build")
    elif has_steps:
        _validate_recipe_step_list(data["steps"], "steps")
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

    if "test" in data and data.get("test") is not None:
        _validate_recipe_step_list(data["test"], "test")

    return data


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


def _workstation_name_for_staged_test(build_id: str) -> str:
    """Deterministic workstation Name tag for the AMI test instance (launched from registered AMI)."""
    bid = _normalize_build_id_arg(build_id)
    base = bid.lower()
    base = re.sub(r"[^a-z0-9-]+", "-", base).strip("-") or "test"
    base = base[:220]
    return f"ami-test-{base}"


def _archive_staged_ami_build_prefix(build_id: str, *, stack: str) -> None:
    """Move ami-builds/<id>/ to ami-build-archive/<id>/ (same layout as ``desk ami build cancel``)."""
    aws = get_desk_settings().aws_settings
    bid = _normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")
    try:
        archive_ami_build(build_id, stack=stack, region=aws.region, profile=aws.profile)
    except AmiBuildNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except AmiBuildError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Archived AMI build {bid} to s3:/{AMI_BUILD_ARCHIVE_PREFIX}{bid}/")


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

    def _upload_step_list(
        step_list: list[dict[str, Any]],
        *,
        label: str,
        run_prefix: str,
        copy_prefix: str,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, step in enumerate(step_list):
            if "run" in step:
                cmd = step["run"]
                resolved, is_file = _resolve_run_for_build(cmd, config_dir)
                if is_file:
                    rel = _artifact_rel_path(resolved, config_dir)
                    key = f"{prefix}files/{run_prefix}/{i}/{rel}"
                    s3.upload_file(resolved, bucket, key)
                    out.append({"run": _s3_uri_for_key(key)})
                else:
                    out.append({"run": cmd})
            else:
                item = dict(step["copy"])
                src = item["source"]
                recursive = item.get("recursive", False)
                resolved = _resolve_copy_source(src, config_dir)
                if not os.path.exists(resolved):
                    raise click.ClickException(
                        f"{label} copy step {i}: source path does not exist: {resolved}"
                    )
                if os.path.isdir(resolved):
                    if not recursive:
                        raise click.ClickException(
                            f"{label} copy step {i}: source is a directory; set \"recursive\": true."
                        )
                tar_path = _write_ami_copy_tarball(
                    resolved, item["dest"], recursive=recursive
                )
                try:
                    key = f"{prefix}files/{copy_prefix}/{i}/{AMI_COPY_BUNDLE_NAME}"
                    s3.upload_file(tar_path, bucket, key)
                finally:
                    os.unlink(tar_path)
                item["source"] = _s3_uri_for_key(key)
                out.append({"copy": item})
        return out

    normalized_build = _upload_step_list(
        steps, label="Build", run_prefix="run", copy_prefix="copy"
    )
    test_steps = _get_test_steps(config)
    normalized_test = _upload_step_list(
        test_steps, label="Test", run_prefix="test-run", copy_prefix="test-copy"
    )

    out_config: dict[str, Any] = {
        "ami_name": ami_name,
        "instance_type": config.get("instance_type", "t3.medium"),
        "build": normalized_build,
        "test": normalized_test,
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


def _stream_ssm_invocation_follow(
    instance_id: str,
    command_id: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Print stdout/stderr as they grow until the SSM command finishes (cf. ``run_script_on_instance``)."""
    terminal_states = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    last_stdout = 0
    last_stderr = 0
    while True:
        result = get_command_invocation(
            command_id, instance_id, region=region, profile=profile
        )
        if len(result.stdout) > last_stdout:
            click.echo(result.stdout[last_stdout:], nl=False)
            last_stdout = len(result.stdout)
        if len(result.stderr) > last_stderr:
            click.echo(result.stderr[last_stderr:], nl=False, err=True)
            last_stderr = len(result.stderr)
        if result.status in terminal_states:
            break
        time.sleep(1)
    click.echo(err=True)
    click.echo()


def _print_verbose_recipe_command_io(
    snap: AsyncAmiBuildSnapshot,
    recipe_eval: AsyncRecipeEval,
    *,
    region: str | None,
    profile: str | None,
    instance_id_override: str | None = None,
) -> None:
    """Print SSM command script and invocation stdout/stderr (for ``status --verbose`` / ``step --verbose``)."""
    iid = instance_id_override or snap.recorded_instance_id
    if not iid:
        return
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
        test_eval = _maybe_evaluate_async_test_recipe(snap)

        if snap.ami_result and snap.ami_result.get("test_failed"):
            _print_async_ami_build_status(
                snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=False
            )
            raise click.ClickException(
                "Test phase failed. The test instance was left running. "
                "Fix in AWS or `desk ami build cancel` before staging a new build."
            )

        if recipe_eval is not None and recipe_eval.blocked:
            _print_async_ami_build_status(
                snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=False
            )
            raise click.ClickException(
                "Recipe step failed. Run `desk ami build step --retry`, or "
                "`desk ami build cancel` to archive this build."
            )

        if test_eval is not None and test_eval.blocked:
            _print_async_ami_build_status(
                snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=False
            )
            raise click.ClickException(
                "Test step failed. The test instance was left running. "
                "`desk ami build step` will take no further action until you cancel the build."
            )

        if (
            snap.recorded_instance_id
            and recipe_eval is not None
            and recipe_eval.in_progress_step_index is not None
            and recipe_eval.in_progress_command_id
        ):
            _stream_ssm_invocation_follow(
                snap.recorded_instance_id,
                recipe_eval.in_progress_command_id,
                region=region,
                profile=profile,
            )
            continue

        if (
            snap.test_recorded_instance_id
            and test_eval is not None
            and test_eval.in_progress_step_index is not None
            and test_eval.in_progress_command_id
        ):
            _stream_ssm_invocation_follow(
                snap.test_recorded_instance_id,
                test_eval.in_progress_command_id,
                region=region,
                profile=profile,
            )
            continue

        _print_async_ami_build_status(
            snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=False
        )
        _run_async_ami_build_step(
            snap,
            recipe_eval=recipe_eval,
            test_eval=test_eval,
            retry=False,
            no_wait=no_wait,
        )
        time.sleep(2)


def _resolve_async_ami_build_snapshot(build_id: str, *, stack: str) -> AsyncAmiBuildSnapshot:
    aws = get_desk_settings().aws_settings
    try:
        return resolve_ami_build_snapshot(
            build_id,
            stack=stack,
            archived=False,
            region=aws.region,
            profile=aws.profile,
        )
    except AmiBuildNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except AmiBuildError as e:
        raise click.ClickException(str(e)) from e


def _print_async_post_recipe_section(snap: AsyncAmiBuildSnapshot) -> None:
    """Post-build AMI registration lines (after build recipe steps are done).

    Uses only fields on ``snap`` (AMI state is resolved once in `_resolve_async_ami_build_snapshot`).
    """
    click.echo("  Post-build (AMI):")
    image_id = snap.registered_ami_id

    if snap.async_pipeline_fully_complete and image_id:
        click.echo(f"    Pipeline: complete (registered {image_id}).")
        return

    if not image_id:
        click.echo(
            "    Next: `desk ami build step` creates the AMI from the builder, then (when the "
            "AMI is available) tags it, terminates the builder, and runs the test phase (if any)."
        )
        return

    st = snap.registered_ami_state
    click.echo(f"    Image: {image_id}")
    click.echo(f"    AMI state (AWS): {st or 'unknown'}")
    if st == "available":
        click.echo(
            "    Next: `desk ami build step` will tag the AMI, terminate the builder, then "
            "launch the test instance (if configured)."
        )
    elif st in ("failed", "error", "deregistered") or st is None:
        click.echo("    AMI creation did not succeed; fix the problem in AWS or `desk ami build cancel`.")
    else:
        click.echo(
            "    Next: run `desk ami build status` or `step` again when the AMI is available."
        )


def _print_test_recipe_status_lines(
    snap: AsyncAmiBuildSnapshot,
    test_eval: AsyncRecipeEval | None,
    *,
    verbose: bool,
) -> None:
    """Print test instance and test-recipe progress (after the builder is gone)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    steps = _get_test_steps(snap.config)
    if not steps:
        return
    click.echo("  Test phase:")
    if not snap.test_recorded_instance_id:
        click.echo("    Test instance id (S3): (not recorded yet)")
        click.echo(
            f"    Next: `desk ami build step` will launch a test instance and write {TEST_INSTANCE_KEY}."
        )
        return
    click.echo(f"    Test instance id (S3): {snap.test_recorded_instance_id}")
    if snap.test_ec2_missing:
        click.echo("    EC2: test instance not found")
        return
    assert snap.test_ec2_state is not None
    click.echo(f"    EC2 state: {snap.test_ec2_state}")
    if snap.test_ec2_state == "terminated":
        click.echo("    SSM ready: n/a")
        return
    if snap.test_ssm_ready is None:
        click.echo("    SSM ready: n/a")
    else:
        click.echo(f"    SSM ready: {'yes' if snap.test_ssm_ready else 'no'}")
    click.echo()
    if snap.test_ec2_state in ("running", "pending") and snap.test_ssm_ready is True:
        ev = test_eval if test_eval is not None else _maybe_evaluate_async_test_recipe(snap)
        if ev is None:
            return
        click.echo("  Test recipe:")
        click.echo(f"    Steps in config: {ev.total_steps}")
        if ev.recipe_complete:
            click.echo("    State: all test steps completed successfully.")
            if ev.total_steps > 0:
                last = steps[ev.total_steps - 1]
                click.echo(
                    f"    Last completed: step {ev.total_steps - 1} — "
                    f"{_describe_recipe_step_for_status(last)}"
                )
        elif ev.blocked and ev.blocked_step_index is not None:
            bad = steps[ev.blocked_step_index]
            click.echo(
                f"    State: failed at test step {ev.blocked_step_index} — "
                f"{_describe_recipe_step_for_status(bad)}"
            )
            if ev.last_error:
                click.echo(f"    Last error: {ev.last_error}")
            if verbose and snap.test_recorded_instance_id:
                _print_verbose_recipe_command_io(
                    snap,
                    ev,
                    region=region,
                    profile=profile,
                    instance_id_override=snap.test_recorded_instance_id,
                )
        elif ev.in_progress_step_index is not None:
            cur = steps[ev.in_progress_step_index]
            click.echo(
                f"    State: test step {ev.in_progress_step_index} in progress — "
                f"{_describe_recipe_step_for_status(cur)}"
            )
            click.echo(f"    SSM command_id: {ev.in_progress_command_id!r}")
            if verbose and snap.test_recorded_instance_id:
                _print_verbose_recipe_command_io(
                    snap,
                    ev,
                    region=region,
                    profile=profile,
                    instance_id_override=snap.test_recorded_instance_id,
                )
        elif ev.next_step_index is not None:
            nxt = steps[ev.next_step_index]
            click.echo(
                f"    Next: test step {ev.next_step_index} — "
                f"{_describe_recipe_step_for_status(nxt)}"
            )
            click.echo("    (`desk ami build step` to start it.)")


def _print_async_ami_build_status(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    test_eval: AsyncRecipeEval | None = None,
    verbose: bool = False,
) -> None:
    """Human-readable status for staged AMI build (also used at the start of `step`).

    Pass ``recipe_eval`` / ``test_eval`` from `ami build step` so status matches the step logic.
    """
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
            _print_async_post_recipe_section(snap)
        elif snap.ami_result and snap.ami_result.get("test_failed"):
            click.echo("  Test phase failed earlier; see ami-result.json.")
        elif _needs_post_builder_test_work(snap) or snap.test_recorded_instance_id:
            _print_test_recipe_status_lines(snap, test_eval, verbose=verbose)
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
            _print_async_post_recipe_section(snap)
        elif snap.ami_result and snap.ami_result.get("test_failed"):
            click.echo("  Test phase failed earlier; see ami-result.json.")
        elif _needs_post_builder_test_work(snap) or snap.test_recorded_instance_id:
            _print_test_recipe_status_lines(snap, test_eval, verbose=verbose)
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
        click.echo("  Build recipe:")
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
            _print_async_post_recipe_section(snap)
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
            _print_async_post_recipe_section(snap)
        else:
            click.echo(
                "The builder instance is not in a running state; fix the instance or terminate "
                "and archive the build."
            )


def _execute_post_builder_test_phase(
    snap: AsyncAmiBuildSnapshot,
    *,
    test_eval: AsyncRecipeEval | None,
    retry: bool,
    no_wait: bool,
) -> None:
    """After the builder is gone: launch test instance, run test recipe, finish pipeline."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    session = boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    image_id = snap.registered_ami_id
    if not image_id:
        raise click.ClickException("Internal error: expected registered AMI id for test phase.")

    if not snap.test_recorded_instance_id:
        ws = _workstation_name_for_staged_test(snap.build_id)
        it = snap.config.get("instance_type", "t3.medium")
        click.echo()
        click.echo(f"Creating test instance {ws!r} from AMI {image_id}...")
        try:
            instance_id, _shutdown = create_workstation(
                ws,
                it,
                ami_id=image_id,
                shutdown_after="4h",
                allow_untested_ami=True,
                region=region,
                profile=profile,
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        key = f"{snap.s3_prefix}{TEST_INSTANCE_KEY}"
        _put_s3_object_json(
            s3,
            snap.bucket,
            key,
            {"instance_id": instance_id},
        )
        click.echo(f"Recorded test instance {instance_id} in s3://{snap.bucket}/{key}")
        click.secho("Step complete: test instance created.", fg="green")
        return

    assert snap.test_recorded_instance_id is not None
    if snap.test_ec2_missing:
        raise click.ClickException(
            "Test instance recorded in S3 is no longer visible in EC2. Investigate or `desk ami build cancel`."
        )
    if snap.test_ec2_state == "terminated":
        if snap.async_pipeline_fully_complete:
            return
        raise click.ClickException(
            "Test instance has terminated unexpectedly; investigate or `desk ami build cancel`."
        )

    if snap.test_ec2_state in ("running", "pending") and snap.test_ssm_ready is True:
        if test_eval is None:
            raise click.ClickException(
                "Internal error: test recipe evaluation was not provided for an SSM-ready test instance."
            )
        tev = test_eval
        steps = _get_test_steps(snap.config)
        if retry:
            if tev.recipe_complete:
                raise click.ClickException(
                    "Nothing to retry: all test steps already completed successfully."
                )
            if tev.in_progress_step_index is not None:
                raise click.ClickException(
                    f"Cannot use --retry while test step {tev.in_progress_step_index} "
                    "is still in progress on SSM."
                )
            if not tev.blocked or tev.blocked_step_index is None:
                raise click.ClickException(
                    "Nothing to retry: there is no failed test step (see `desk ami build status`)."
                )
            step = steps[tev.blocked_step_index]
            kind = "run" if "run" in step else "copy"
            shell = _expected_async_shell_for_step(
                step,
                tev.blocked_step_index,
                bucket=snap.bucket,
                region=region,
                profile=profile,
            )
            comment = _ami_test_comment_tag(snap.build_id, tev.blocked_step_index, kind)
            command_id = send_ssm_command(
                snap.test_recorded_instance_id,
                shell,
                region=region,
                profile=profile,
                timeout_seconds=7200,
                comment=comment,
            )
            click.echo()
            click.echo(
                f"Retrying test step {tev.blocked_step_index} ({kind}): SSM command_id={command_id}"
            )
            click.secho(
                "Step initiated (not waiting for completion). Check `desk ami build status`.",
                fg="green",
            )
            return
        if tev.blocked:
            _merge_ami_result_s3(
                s3,
                snap.bucket,
                snap.s3_prefix,
                {"test_failed": True},
            )
            click.echo()
            click.echo(
                "Test step failed. The test instance was left running. "
                "`desk ami build step` will take no further actions until you `desk ami build cancel` "
                "or resolve the failure outside desk."
            )
            if tev.last_error:
                click.echo(f"Last error: {tev.last_error}")
            return
        if tev.in_progress_step_index is not None:
            click.echo()
            click.echo(
                f"(No step taken: test step {tev.in_progress_step_index} is still in progress on SSM.)"
            )
            return
        if tev.recipe_complete:
            tag_ami_build_status(
                image_id,
                AMI_BUILD_STATUS_TESTED,
                region=region,
                profile=profile,
            )
            terminate_instance(snap.test_recorded_instance_id, region=region, profile=profile)
            _merge_ami_result_s3(
                s3,
                snap.bucket,
                snap.s3_prefix,
                {"pipeline_complete": True},
            )
            click.echo()
            click.secho(
                f"Tests passed; tagged AMI {image_id} as tested and terminated test instance "
                f"{snap.test_recorded_instance_id}.",
                fg="green",
            )
            return
        if tev.next_step_index is None:
            click.echo()
            click.echo("(No step taken.)")
            return
        step = steps[tev.next_step_index]
        kind = "run" if "run" in step else "copy"
        shell = _expected_async_shell_for_step(
            step,
            tev.next_step_index,
            bucket=snap.bucket,
            region=region,
            profile=profile,
        )
        comment = _ami_test_comment_tag(snap.build_id, tev.next_step_index, kind)
        command_id = send_ssm_command(
            snap.test_recorded_instance_id,
            shell,
            region=region,
            profile=profile,
            timeout_seconds=7200,
            comment=comment,
        )
        click.echo()
        click.echo(
            f"Started test step {tev.next_step_index} ({kind}): SSM command_id={command_id}"
        )
        click.secho(
            "Step initiated (not waiting for completion). Check `desk ami build status`.",
            fg="green",
        )
        return

    if snap.test_ec2_state in ("running", "pending") and snap.test_ssm_ready is False:
        click.echo()
        click.echo("(No step taken: waiting for SSM on the test instance.)")
        return
    if snap.test_ec2_state in ("stopped", "stopping", "shutting-down"):
        click.echo()
        click.echo("(No step taken: test instance is not running.)")
        return
    _ = no_wait  # reserved for symmetry with builder phase
    raise click.ClickException(
        f"Unexpected test instance state (test_ec2_state={snap.test_ec2_state!r}, "
        f"test_ec2_missing={snap.test_ec2_missing})."
    )


def _run_async_ami_build_step(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    test_eval: AsyncRecipeEval | None = None,
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

    if snap.ami_result and snap.ami_result.get("test_failed"):
        click.echo()
        click.echo(
            "Test phase failed earlier; `desk ami build step` is a no-op until you "
            "`desk ami build cancel` or fix the situation in AWS."
        )
        return

    if snap.recorded_instance_id:
        if snap.ec2_missing or snap.ec2_state == "terminated":
            if snap.async_pipeline_fully_complete:
                click.echo()
                click.secho("AMI build pipeline already complete.", fg="green")
                return
            if _needs_post_builder_test_work(snap):
                return _execute_post_builder_test_phase(
                    snap,
                    test_eval=test_eval,
                    retry=retry,
                    no_wait=no_wait,
                )
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
                click.echo()
                click.echo(
                    f"Retrying recipe step {ev.blocked_step_index} ({kind}): "
                    f"SSM command_id={command_id}"
                )
                click.secho(
                    "Step initiated (not waiting for completion). Check `desk ami build status`.",
                    fg="green",
                )
                return
            if ev.blocked:
                click.echo()
                click.echo(
                    "Recipe step failed. Run `desk ami build cancel` to archive this build, "
                    "or `desk ami build step --retry` to re-send the failed step, "
                    "then fix the recipe and stage a new build if needed."
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
                    click.echo()
                    click.echo(
                        f"Started AMI registration: {new_image_id} (name={reg_name!r})."
                    )
                    click.secho(
                        "Not waiting for availability. Check `desk ami build status`, then run "
                        "`desk ami build step` again when the AMI is available to terminate the builder.",
                        fg="green",
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
                        tsteps = _get_test_steps(snap.config)
                        if tsteps:
                            tag_ami_build_status(
                                image_id,
                                AMI_BUILD_STATUS_UNTESTED,
                                region=region,
                                profile=profile,
                            )
                        else:
                            tag_ami_build_status(
                                image_id,
                                AMI_BUILD_STATUS_TESTED,
                                region=region,
                                profile=profile,
                            )
                        merge_updates: dict[str, Any] = {}
                        if not tsteps:
                            merge_updates["pipeline_complete"] = True
                        if merge_updates:
                            _merge_ami_result_s3(
                                s3,
                                snap.bucket,
                                snap.s3_prefix,
                                merge_updates,
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
                tsteps = _get_test_steps(snap.config)
                if tsteps:
                    tag_ami_build_status(
                        image_id,
                        AMI_BUILD_STATUS_UNTESTED,
                        region=region,
                        profile=profile,
                    )
                else:
                    tag_ami_build_status(
                        image_id,
                        AMI_BUILD_STATUS_TESTED,
                        region=region,
                        profile=profile,
                    )
                terminate_instance(snap.recorded_instance_id, region=region, profile=profile)
                if not tsteps:
                    _merge_ami_result_s3(
                        s3,
                        snap.bucket,
                        snap.s3_prefix,
                        {"pipeline_complete": True},
                    )
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Include SSM command script and invocation stdout/stderr for the active or failed step.",
)
def ami_build_status(build_id: str, stack: str, verbose: bool) -> None:
    """Show staged AMI build progress from S3, EC2, and SSM Run Command history (quick; does not wait).

    Recipe progress is derived from SSM commands on the builder instance (Comment tag and/or
    command body match). After a step fails, use `desk ami build step --retry` or archive with
    `desk ami build cancel` before staging a new build.
    """
    snap = _resolve_async_ami_build_snapshot(build_id, stack=stack)
    recipe_eval = _maybe_evaluate_async_recipe(snap)
    test_eval = _maybe_evaluate_async_test_recipe(snap)
    _print_async_ami_build_status(
        snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=verbose
    )


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
    help="Include SSM command script and invocation stdout/stderr for the active or failed step.",
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
    test_eval = _maybe_evaluate_async_test_recipe(snap)
    _print_async_ami_build_status(
        snap, recipe_eval=recipe_eval, test_eval=test_eval, verbose=verbose
    )
    _run_async_ami_build_step(
        snap, recipe_eval=recipe_eval, test_eval=test_eval, retry=retry
    )


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
      build: list of build steps; each step is {\"run\": \"cmd\"} or {\"copy\": {\"source\": \"...\", \"dest\": \"...\", \"recursive\": optional}}.
      test (optional): same shape; runs on a new instance launched from the registered AMI after the builder is terminated.
      Alternatively use legacy \"steps\" instead of \"build\", or legacy copy + run + optional run_before_copy.
      ami_name: base name for the registered AMI (async builds append the build id for uniqueness).

    This command uses the same S3 + SSM pipeline as ``desk ami build create`` / ``step`` (not
    direct SCP). Remote command output is streamed from SSM while steps run. On success the staged
    prefix is moved to ami-build-archive/. On failure the build is left under ami-builds/ for
    debugging.

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
            click.echo(f"Resuming AMI build {bid}")
            click.echo(f"  s3:/{AMI_BUILDS_PREFIX}{bid}/")
            click.echo(f"  Builder name: {_workstation_name_for_staged_build(bid)}")
            click.echo()
            _drive_ami_build_run_loop(bid, stack=stack, no_wait=no_wait)
        else:
            assert config_file is not None
            build_id, bucket, prefix = _stage_ami_build_to_s3(config_file, stack=stack)
            bid = build_id
            click.echo(f"Staged AMI build {build_id}")
            click.echo(f"  s3:/{prefix}")
            click.echo(f"  Bucket: s3://{bucket}/{prefix}")
            click.echo()
            click.echo(f"Building AMI from config: {config_file}")
            click.echo(f"  Builder name: {_workstation_name_for_staged_build(build_id)}")
            click.echo()
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

    Writes a normalized config (``build`` / ``test``) whose copy steps stage a ``bundle.tar`` per step
    (preserving Unix modes) and whose run paths reference s3:/ keys under ami-builds/<build-id>/.
    """
    build_id, bucket, prefix = _stage_ami_build_to_s3(config_file, stack=stack)
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
