"""Drive `desk ami build run` until the pipeline completes."""

from __future__ import annotations

import time

import click

from desk.config import get_desk_settings

from desk_cli.ami_build.archive import archive_staged_ami_build_prefix
from desk_cli.ami_build.build_config import normalize_build_id_arg
from desk_cli.ami_build.recipe_eval import maybe_evaluate_async_recipe
from desk_cli.ami_build.snapshot import resolve_async_ami_build_snapshot
from desk_cli.ami_build.status_display import (
    print_async_ami_build_status,
    stream_ssm_invocation_follow,
)
from desk_cli.ami_build.step_engine import run_async_ami_build_step


def drive_ami_build_run_loop(
    build_id: str,
    *,
    stack: str,
    no_wait: bool,
) -> None:
    """Orchestrate staged create + step loop until pipeline complete, then archive."""
    aws = get_desk_settings().aws_settings
    region = aws.region
    profile = aws.profile
    bid = normalize_build_id_arg(build_id)

    while True:
        snap = resolve_async_ami_build_snapshot(bid, stack=stack)
        if snap.async_pipeline_fully_complete:
            archive_staged_ami_build_prefix(bid, stack=stack)
            click.secho("AMI build complete.", fg="green", bold=True)
            return

        recipe_eval = maybe_evaluate_async_recipe(snap)

        if recipe_eval is not None and recipe_eval.blocked:
            print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=False)
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
            stream_ssm_invocation_follow(
                snap.recorded_instance_id,
                recipe_eval.in_progress_command_id,
                region=region,
                profile=profile,
            )
            continue

        print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=False)
        run_async_ami_build_step(
            snap,
            recipe_eval=recipe_eval,
            retry=False,
            no_wait=no_wait,
        )
        time.sleep(2)
