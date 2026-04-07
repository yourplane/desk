"""Derive recipe progress from SSM Run Command history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import click
from botocore.exceptions import ClientError

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.build_config import get_build_steps
from desk_cli.ami_build.shell import (
    expected_async_shell_for_step,
    normalize_shell_for_compare,
    parse_ami_build_comment,
)
from desk_cli.ami_build.snapshot import AsyncAmiBuildSnapshot


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


def invocation_step_failed(status: str, exit_code: int | None) -> bool:
    if status in ("Failed", "TimedOut", "Cancelled", "Cancelling"):
        return True
    if status == "Success":
        if exit_code is None:
            return False
        return exit_code != 0
    return False


def invocation_step_succeeded(status: str, exit_code: int | None) -> bool:
    return status == "Success" and (exit_code is None or exit_code == 0)


def map_invocation_to_step_index(
    command_id: str,
    *,
    build_id: str,
    steps: list[dict[str, Any]],
    bucket: str,
    region: str | None,
    profile: str | None,
) -> int | None:
    try:
        cmd_doc = aws_shim.get_ssm_command(command_id, region=region, profile=profile)
    except (ClientError, RuntimeError):
        return None
    if cmd_doc.get("DocumentName") != "AWS-RunShellScript":
        return None
    params = cmd_doc.get("Parameters") or {}
    commands = params.get("commands")
    if not commands or not isinstance(commands, list):
        return None
    shell = commands[0] if commands else ""
    parsed = parse_ami_build_comment(cmd_doc.get("Comment"), build_id)
    if parsed is not None:
        return parsed[0]
    norm = normalize_shell_for_compare(shell)
    for i, step in enumerate(steps):
        try:
            expected = expected_async_shell_for_step(
                step, i, bucket=bucket, region=region, profile=profile
            )
        except click.ClickException:
            continue
        if norm == normalize_shell_for_compare(expected):
            return i
    return None


def evaluate_async_recipe(
    instance_id: str,
    *,
    build_id: str,
    config: dict[str, Any],
    bucket: str,
    region: str | None,
    profile: str | None,
) -> AsyncRecipeEval:
    steps = get_build_steps(config)
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

    inv_rows = aws_shim.list_command_invocations_for_instance(
        instance_id, region=region, profile=profile
    )
    by_step: dict[int, dict[str, Any]] = {}
    for row in inv_rows:
        cid = row.get("CommandId")
        if not cid:
            continue
        step_i = map_invocation_to_step_index(
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
                inv = aws_shim.get_command_invocation(
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
        if invocation_step_failed(st, exit_code):
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
        if not invocation_step_succeeded(st, exit_code):
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


def maybe_evaluate_async_recipe(snap: AsyncAmiBuildSnapshot) -> AsyncRecipeEval | None:
    """Load SSM recipe state when the builder can run steps; otherwise return None."""
    if not snap.recorded_instance_id:
        return None
    if snap.ec2_missing or snap.ec2_state == "terminated":
        return None
    if snap.ec2_state not in ("running", "pending"):
        return None
    if snap.ssm_ready is not True:
        return None
    aws = get_desk_settings().aws_settings
    return evaluate_async_recipe(
        snap.recorded_instance_id,
        build_id=snap.build_id,
        config=snap.config,
        bucket=snap.bucket,
        region=aws.region,
        profile=aws.profile,
    )
