"""Plan and execute one async AMI build workflow step (unified step kinds + static registry)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

import click

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import BUILDER_INSTANCE_KEY
from desk_cli.ami_build.build_config import (
    get_build_steps,
    registration_ami_name_for_async_build,
    workstation_name_for_staged_build,
)
from desk_cli.ami_build.recipe_eval import AsyncRecipeEval
from desk_cli.ami_build import s3_utils as ami_s3
from desk_cli.ami_build.shell import ami_build_comment_tag, expected_async_shell_for_step
from desk_cli.ami_build.snapshot import AsyncAmiBuildSnapshot


class AsyncWorkflowStepKind(str, Enum):
    """All workflow step types: recipe SSM and infra (builder, AMI, terminate)."""

    CREATE_BUILDER = "create_builder"
    RETRY_RECIPE_SSM = "retry_recipe_ssm"
    SEND_NEXT_RECIPE_SSM = "send_next_recipe_ssm"
    REGISTER_AMI = "register_ami"
    TERMINATE_BUILDER = "terminate_builder"
    TERMINATE_BUILDER_NO_WAIT = "terminate_builder_no_wait"
    NOOP_PIPELINE_COMPLETE = "noop_pipeline_complete"
    NOOP_RECIPE_BLOCKED_HINT = "noop_recipe_blocked_hint"
    NOOP_IN_PROGRESS = "noop_in_progress"
    NOOP_WAITING_SSM = "noop_waiting_ssm"
    NOOP_AMI_PENDING = "noop_ami_pending"
    NOOP_NO_NEXT = "noop_no_next"
    NOOP_STOPPED_NOT_RUNNING = "noop_stopped_not_running"
    NOOP_STOPPED_PIPELINE_COMPLETE = "noop_stopped_pipeline_complete"
    FAIL_BUILDER_GONE = "fail_builder_gone"
    FAIL_INTERNAL_NO_RECIPE_EVAL = "fail_internal_no_recipe_eval"
    FAIL_UNEXPECTED_EC2 = "fail_unexpected_ec2"


def plan_async_build_step(
    snap: AsyncAmiBuildSnapshot,
    ev: AsyncRecipeEval | None,
    *,
    retry: bool,
    no_wait: bool,
) -> tuple[AsyncWorkflowStepKind, dict[str, Any]]:
    """Decide which single workflow step to run (mirrors legacy _run_async_ami_build_step)."""
    if snap.recorded_instance_id:
        if snap.ec2_missing or snap.ec2_state == "terminated":
            if snap.async_pipeline_fully_complete:
                return (AsyncWorkflowStepKind.NOOP_PIPELINE_COMPLETE, {})
            return (AsyncWorkflowStepKind.FAIL_BUILDER_GONE, {})

        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is True:
            if ev is None:
                return (AsyncWorkflowStepKind.FAIL_INTERNAL_NO_RECIPE_EVAL, {})
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
                step = get_build_steps(snap.config)[ev.blocked_step_index]
                kind = "run" if "run" in step else "copy"
                return (
                    AsyncWorkflowStepKind.RETRY_RECIPE_SSM,
                    {"step_index": ev.blocked_step_index, "recipe_kind": kind},
                )
            if ev.blocked:
                return (
                    AsyncWorkflowStepKind.NOOP_RECIPE_BLOCKED_HINT,
                    {"last_error": ev.last_error},
                )
            if ev.in_progress_step_index is not None:
                return (
                    AsyncWorkflowStepKind.NOOP_IN_PROGRESS,
                    {"step_index": ev.in_progress_step_index},
                )
            if ev.recipe_complete:
                if snap.async_pipeline_fully_complete:
                    return (AsyncWorkflowStepKind.NOOP_PIPELINE_COMPLETE, {})
                image_id = snap.registered_ami_id
                if not image_id:
                    return (AsyncWorkflowStepKind.REGISTER_AMI, {})
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
                        return (
                            AsyncWorkflowStepKind.TERMINATE_BUILDER_NO_WAIT,
                            {"image_id": image_id, "ami_state": st},
                        )
                    return (
                        AsyncWorkflowStepKind.NOOP_AMI_PENDING,
                        {"image_id": image_id, "ami_state": st},
                    )
                return (AsyncWorkflowStepKind.TERMINATE_BUILDER, {"image_id": image_id})
            if ev.next_step_index is None:
                return (AsyncWorkflowStepKind.NOOP_NO_NEXT, {})
            step = get_build_steps(snap.config)[ev.next_step_index]
            kind = "run" if "run" in step else "copy"
            return (
                AsyncWorkflowStepKind.SEND_NEXT_RECIPE_SSM,
                {"step_index": ev.next_step_index, "recipe_kind": kind},
            )

        if snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
            return (AsyncWorkflowStepKind.NOOP_WAITING_SSM, {})

        if snap.ec2_state in ("stopped", "stopping", "shutting-down"):
            if snap.async_pipeline_fully_complete:
                return (AsyncWorkflowStepKind.NOOP_STOPPED_PIPELINE_COMPLETE, {})
            return (AsyncWorkflowStepKind.NOOP_STOPPED_NOT_RUNNING, {})

        raise click.ClickException(
            f"Unexpected builder state (ec2_state={snap.ec2_state!r}, "
            f"ec2_missing={snap.ec2_missing})."
        )

    return (AsyncWorkflowStepKind.CREATE_BUILDER, {})


def run_async_ami_build_step(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    retry: bool = False,
    no_wait: bool = False,
) -> None:
    """Perform at most one quick action for `desk ami build step` (after status output)."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile

    kind, payload = plan_async_build_step(
        snap, recipe_eval, retry=retry, no_wait=no_wait
    )
    _WORKFLOW_REGISTRY[kind](snap, recipe_eval, payload, region=region, profile=profile)


def _exec_create_builder(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    instance_type = snap.config.get("instance_type", "t3.medium")
    workstation_name = workstation_name_for_staged_build(snap.build_id)
    builder_ami = aws_shim.get_latest_ubuntu_ami(region=region, profile=profile)
    click.echo()
    click.echo(f"Creating builder instance {workstation_name!r}...")
    try:
        instance_id, _shutdown = aws_shim.create_workstation(
            workstation_name,
            instance_type,
            ami_id=builder_ami,
            shutdown_after="4h",
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    key = f"{snap.s3_prefix}{BUILDER_INSTANCE_KEY}"
    ami_s3.put_s3_object_json(
        s3,
        snap.bucket,
        key,
        {"instance_id": instance_id},
    )
    click.echo(f"Recorded {instance_id} in s3://{snap.bucket}/{key}")
    click.secho("Step complete: builder instance created and id written to S3.", fg="green")


def _exec_send_recipe_ssm(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
    label: str,
) -> None:
    step_index = payload["step_index"]
    steps = get_build_steps(snap.config)
    step = steps[step_index]
    shell = expected_async_shell_for_step(
        step,
        step_index,
        bucket=snap.bucket,
        region=region,
        profile=profile,
    )
    comment = ami_build_comment_tag(snap.build_id, step_index, payload["recipe_kind"])
    assert snap.recorded_instance_id
    command_id = aws_shim.send_ssm_command(
        snap.recorded_instance_id,
        shell,
        region=region,
        profile=profile,
        timeout_seconds=7200,
        comment=comment,
    )
    click.echo()
    click.echo(f"{label}: SSM command_id={command_id}")
    click.secho(
        "Step initiated (not waiting for completion). Check `desk ami build status`.",
        fg="green",
    )


def _exec_retry_recipe_ssm(
    snap: AsyncAmiBuildSnapshot,
    ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _exec_send_recipe_ssm(
        snap,
        ev,
        payload,
        region=region,
        profile=profile,
        label=(
            f"Retrying recipe step {payload['step_index']} ({payload['recipe_kind']})"
        ),
    )


def _exec_send_next_recipe_ssm(
    snap: AsyncAmiBuildSnapshot,
    ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _exec_send_recipe_ssm(
        snap,
        ev,
        payload,
        region=region,
        profile=profile,
        label=(
            f"Started recipe step {payload['step_index']} ({payload['recipe_kind']})"
        ),
    )


def _exec_register_ami(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    ami_name = snap.config.get("ami_name")
    if not ami_name or not isinstance(ami_name, str):
        raise click.ClickException("Config must specify 'ami_name'.")
    reg_name = registration_ami_name_for_async_build(ami_name.strip(), snap.build_id)
    assert snap.recorded_instance_id
    new_image_id = aws_shim.create_ami(
        snap.recorded_instance_id,
        name=reg_name,
        description=f"desk async AMI build {snap.build_id}",
        no_reboot=False,
        region=region,
        profile=profile,
    )
    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    ami_s3.merge_ami_result_s3(
        s3,
        snap.bucket,
        snap.s3_prefix,
        {"image_id": new_image_id},
    )
    click.echo()
    click.echo(f"Started AMI registration: {new_image_id} (name={reg_name!r}).")
    click.secho(
        "Not waiting for availability. Check `desk ami build status`, then run "
        "`desk ami build step` again when the AMI is available to terminate the builder.",
        fg="green",
    )


def _exec_terminate_builder(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    assert snap.recorded_instance_id
    aws_shim.terminate_instance(snap.recorded_instance_id, region=region, profile=profile)
    image_id = payload["image_id"]
    click.echo()
    click.secho(
        f"Terminated builder {snap.recorded_instance_id}; AMI {image_id} is available.",
        fg="green",
    )


def _exec_terminate_builder_no_wait(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    session = aws_shim.boto3.Session(region_name=region, profile_name=profile)
    s3 = session.client("s3")
    assert snap.recorded_instance_id
    ami_s3.merge_ami_result_s3(
        s3,
        snap.bucket,
        snap.s3_prefix,
        {"pipeline_complete": True},
    )
    aws_shim.terminate_instance(snap.recorded_instance_id, region=region, profile=profile)
    image_id = payload["image_id"]
    st = payload["ami_state"]
    click.echo()
    click.secho(
        f"Terminated builder {snap.recorded_instance_id} (--no-wait; "
        f"AMI {image_id} was {st!r}).",
        fg="yellow",
    )


def _exec_noop_pipeline_complete(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.secho("AMI build pipeline already complete.", fg="green")


def _exec_noop_recipe_blocked_hint(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.echo(
        "Recipe step failed. Run `desk ami build cancel` to archive this build, "
        "or `desk ami build step --retry` to re-send the failed step, "
        "then fix the recipe and stage a new build if needed."
    )
    if payload.get("last_error"):
        click.echo(f"Last error: {payload['last_error']}")


def _exec_noop_in_progress(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.echo(
        f"(No step taken: step {payload['step_index']} is still in progress on SSM.)"
    )


def _exec_noop_waiting_ssm(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.echo("(No step taken: waiting for SSM.)")


def _exec_noop_ami_pending(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    image_id = payload["image_id"]
    st = payload["ami_state"]
    click.echo()
    click.echo(
        f"(No step taken: AMI {image_id} is still {st!r}; run `desk ami build step` "
        "again when it is available.)"
    )


def _exec_noop_no_next(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.echo("(No step taken.)")


def _exec_noop_stopped_not_running(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.echo("(No step taken: instance not running.)")


def _exec_noop_stopped_pipeline_complete(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    click.echo()
    click.secho(
        "AMI build pipeline complete (builder stopped or shutting down).",
        fg="green",
    )


def _exec_fail_builder_gone(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = region, profile
    raise click.ClickException(
        "Refusing to create a new builder instance: this build already recorded "
        f"{snap.recorded_instance_id!r}, and that instance is no longer usable. "
        "Use `desk ami build cancel` or investigate in AWS."
    )


def _exec_fail_internal_no_recipe_eval(
    snap: AsyncAmiBuildSnapshot,
    _ev: AsyncRecipeEval | None,
    _payload: dict[str, Any],
    *,
    region: str | None,
    profile: str | None,
) -> None:
    _ = snap, region, profile
    raise click.ClickException(
        "Internal error: recipe evaluation was not provided for an SSM-ready builder; "
        "this is a desk bug."
    )


WorkflowHandler = Callable[..., None]

_WORKFLOW_REGISTRY: dict[AsyncWorkflowStepKind, WorkflowHandler] = {
    AsyncWorkflowStepKind.CREATE_BUILDER: _exec_create_builder,
    AsyncWorkflowStepKind.RETRY_RECIPE_SSM: _exec_retry_recipe_ssm,
    AsyncWorkflowStepKind.SEND_NEXT_RECIPE_SSM: _exec_send_next_recipe_ssm,
    AsyncWorkflowStepKind.REGISTER_AMI: _exec_register_ami,
    AsyncWorkflowStepKind.TERMINATE_BUILDER: _exec_terminate_builder,
    AsyncWorkflowStepKind.TERMINATE_BUILDER_NO_WAIT: _exec_terminate_builder_no_wait,
    AsyncWorkflowStepKind.NOOP_PIPELINE_COMPLETE: _exec_noop_pipeline_complete,
    AsyncWorkflowStepKind.NOOP_RECIPE_BLOCKED_HINT: _exec_noop_recipe_blocked_hint,
    AsyncWorkflowStepKind.NOOP_IN_PROGRESS: _exec_noop_in_progress,
    AsyncWorkflowStepKind.NOOP_WAITING_SSM: _exec_noop_waiting_ssm,
    AsyncWorkflowStepKind.NOOP_AMI_PENDING: _exec_noop_ami_pending,
    AsyncWorkflowStepKind.NOOP_NO_NEXT: _exec_noop_no_next,
    AsyncWorkflowStepKind.NOOP_STOPPED_NOT_RUNNING: _exec_noop_stopped_not_running,
    AsyncWorkflowStepKind.NOOP_STOPPED_PIPELINE_COMPLETE: _exec_noop_stopped_pipeline_complete,
    AsyncWorkflowStepKind.FAIL_BUILDER_GONE: _exec_fail_builder_gone,
    AsyncWorkflowStepKind.FAIL_INTERNAL_NO_RECIPE_EVAL: _exec_fail_internal_no_recipe_eval,
}
