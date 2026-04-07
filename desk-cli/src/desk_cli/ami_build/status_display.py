"""Human-readable status and SSM stream output for async AMI builds."""

from __future__ import annotations

import time

import click
from botocore.exceptions import ClientError

from desk.config import get_desk_settings

from desk_cli.ami_build import aws_shim
from desk_cli.ami_build.constants import BUILDER_INSTANCE_KEY
from desk_cli.ami_build.build_config import describe_recipe_step_for_status, get_build_steps
from desk_cli.ami_build.recipe_eval import AsyncRecipeEval, maybe_evaluate_async_recipe
from desk_cli.ami_build.snapshot import AsyncAmiBuildSnapshot


def stream_ssm_invocation_follow(
    instance_id: str,
    command_id: str,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Print stdout/stderr as they grow until the SSM command finishes."""
    terminal_states = {"Success", "Cancelled", "Failed", "TimedOut", "Cancelling"}
    last_stdout = 0
    last_stderr = 0
    while True:
        result = aws_shim.get_command_invocation(
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


def print_verbose_recipe_command_io(
    snap: AsyncAmiBuildSnapshot,
    recipe_eval: AsyncRecipeEval,
    *,
    region: str | None,
    profile: str | None,
) -> None:
    """Print SSM command script and invocation stdout/stderr (verbose status/step)."""
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
        doc = aws_shim.get_ssm_command(cid, region=region, profile=profile)
        params = doc.get("Parameters") or {}
        cmds = params.get("commands")
        if isinstance(cmds, list) and cmds:
            click.echo("    Command script:")
            for line in str(cmds[0]).splitlines():
                click.echo(f"      {line}")
        inv = aws_shim.get_command_invocation(cid, iid, region=region, profile=profile)
        click.echo("    StandardOutputContent:")
        click.echo(inv.stdout if inv.stdout else "(empty)")
        click.echo("    StandardErrorContent:")
        click.echo(inv.stderr if inv.stderr else "(empty)", err=True)
    except (ClientError, RuntimeError, OSError) as e:
        click.echo(f"    (Could not load SSM invocation details: {e})")


def print_async_post_recipe_section(snap: AsyncAmiBuildSnapshot) -> None:
    """Post-recipe AMI registration lines (after recipe steps are done)."""
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
        click.echo(
            "    AMI creation did not succeed; fix the problem in AWS or `desk ami build cancel`."
        )
    else:
        click.echo(
            "    Next: run `desk ami build status` or `step` again when the AMI is available."
        )


def print_async_ami_build_status(
    snap: AsyncAmiBuildSnapshot,
    *,
    recipe_eval: AsyncRecipeEval | None = None,
    verbose: bool = False,
) -> None:
    """Human-readable status for staged AMI build."""
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
            print_async_post_recipe_section(snap)
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
            print_async_post_recipe_section(snap)
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
        ev = recipe_eval if recipe_eval is not None else maybe_evaluate_async_recipe(snap)
        if ev is None:
            return
        steps = get_build_steps(snap.config)
        click.echo("  Recipe:")
        click.echo(f"    Steps in config: {ev.total_steps}")
        if ev.recipe_complete:
            click.echo("    State: all steps completed successfully.")
            if ev.total_steps > 0:
                last = steps[ev.total_steps - 1]
                click.echo(
                    f"    Last completed: step {ev.total_steps - 1} — "
                    f"{describe_recipe_step_for_status(last)}"
                )
            click.echo()
            print_async_post_recipe_section(snap)
        elif ev.blocked and ev.blocked_step_index is not None:
            bad = steps[ev.blocked_step_index]
            click.echo(
                f"    State: failed at step {ev.blocked_step_index} — "
                f"{describe_recipe_step_for_status(bad)}"
            )
            click.echo(
                "    Hint: `desk ami build step --retry` or `desk ami build cancel`."
            )
            if ev.last_error:
                click.echo(f"    Last error: {ev.last_error}")
            if verbose:
                print_verbose_recipe_command_io(snap, ev, region=region, profile=profile)
        elif ev.in_progress_step_index is not None:
            cur = steps[ev.in_progress_step_index]
            click.echo(
                f"    State: step {ev.in_progress_step_index} in progress — "
                f"{describe_recipe_step_for_status(cur)}"
            )
            click.echo(f"    SSM command_id: {ev.in_progress_command_id!r}")
            if ev.in_progress_step_index > 0:
                prev = steps[ev.in_progress_step_index - 1]
                click.echo(
                    f"    Last completed: step {ev.in_progress_step_index - 1} — "
                    f"{describe_recipe_step_for_status(prev)}"
                )
            if verbose:
                print_verbose_recipe_command_io(snap, ev, region=region, profile=profile)
        elif ev.next_step_index is not None:
            nxt = steps[ev.next_step_index]
            click.echo(
                f"    Next: step {ev.next_step_index} — "
                f"{describe_recipe_step_for_status(nxt)}"
            )
            click.echo("    (`desk ami build step` to start it.)")
            if ev.next_step_index > 0:
                prev = steps[ev.next_step_index - 1]
                click.echo(
                    f"    Last completed: step {ev.next_step_index - 1} — "
                    f"{describe_recipe_step_for_status(prev)}"
                )
    elif snap.ec2_state in ("running", "pending") and snap.ssm_ready is False:
        click.echo(
            "Waiting for SSM on the builder instance. "
            "Run `desk ami build status` or `step` again later (no long waits in this command)."
        )
    elif snap.ec2_state in ("stopped", "stopping", "shutting-down"):
        click.echo()
        if snap.async_pipeline_fully_complete:
            print_async_post_recipe_section(snap)
        else:
            click.echo(
                "The builder instance is not in a running state; fix the instance or terminate "
                "and archive the build."
            )
