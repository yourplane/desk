"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

from typing import Optional

import click

from desk_cli.ami_build import recipe_eval as ami_recipe
from desk_cli.ami_build import run_loop as ami_run_loop
from desk_cli.ami_build import staging as ami_staging
from desk_cli.ami_build.archive import archive_staged_ami_build_prefix
from desk_cli.ami_build.build_config import (
    normalize_build_id_arg,
    workstation_name_for_staged_build,
)
from desk_cli.ami_build.constants import AMI_BUILDS_PREFIX
from desk_cli.ami_build.list_staged import list_staged_builds
from desk_cli.ami_build.snapshot import resolve_async_ami_build_snapshot
from desk_cli.ami_build.status_display import print_async_ami_build_status
from desk_cli.ami_build.step_engine import run_async_ami_build_step
from desk_cli.ami_build.sync_commands import run_ami_create, run_ami_list


@click.group("ami")
def ami_group() -> None:
    """Manage AMIs from desk workstations."""
    pass


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
    """Show staged AMI build progress from S3, EC2, and SSM Run Command history (quick; does not wait)."""
    snap = resolve_async_ami_build_snapshot(build_id, stack=stack)
    recipe_eval = ami_recipe.maybe_evaluate_async_recipe(snap)
    print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=verbose)


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
    """Advance the async AMI build by one quick action, or no-op if there is nothing to do."""
    snap = resolve_async_ami_build_snapshot(build_id, stack=stack)
    recipe_eval = ami_recipe.maybe_evaluate_async_recipe(snap)
    print_async_ami_build_status(snap, recipe_eval=recipe_eval, verbose=verbose)
    run_async_ami_build_step(snap, recipe_eval=recipe_eval, retry=retry)


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

    CONFIG_FILE uses JSON with ``steps`` of ``run`` / ``copy`` entries (see ``desk ami build create``).
    """
    if resume_build_id and config_file:
        raise click.UsageError("Pass either CONFIG_FILE or --continue BUILD_ID, not both.")
    if not resume_build_id and not config_file:
        raise click.UsageError("Missing CONFIG_FILE (or use --continue BUILD_ID).")

    bid = ""
    try:
        if resume_build_id:
            bid = normalize_build_id_arg(resume_build_id)
            if not bid:
                raise click.ClickException("Build id is empty.")
            click.echo(f"Resuming AMI build {bid}")
            click.echo(f"  s3:/{AMI_BUILDS_PREFIX}{bid}/")
            click.echo(f"  Builder name: {workstation_name_for_staged_build(bid)}")
            click.echo()
            ami_run_loop.drive_ami_build_run_loop(bid, stack=stack, no_wait=no_wait)
        else:
            assert config_file is not None
            build_id, bucket, prefix = ami_staging.stage_ami_build_to_s3(config_file, stack=stack)
            bid = build_id
            click.echo(f"Staged AMI build {build_id}")
            click.echo(f"  s3:/{prefix}")
            click.echo(f"  Bucket: s3://{bucket}/{prefix}")
            click.echo()
            click.echo(f"Building AMI from config: {config_file}")
            click.echo(f"  Builder name: {workstation_name_for_staged_build(build_id)}")
            click.echo()
            ami_run_loop.drive_ami_build_run_loop(build_id, stack=stack, no_wait=no_wait)
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
    """Upload an AMI build recipe and local artifacts to a dedicated folder in the desk S3 bucket."""
    build_id, bucket, prefix = ami_staging.stage_ami_build_to_s3(config_file, stack=stack)
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
    list_staged_builds(archived=archived, output=output, stack=stack)


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
    bid = normalize_build_id_arg(build_id)
    if not bid:
        raise click.ClickException("Build id is empty.")
    try:
        archive_staged_ami_build_prefix(bid, stack=stack)
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
    """List AMIs created from desk workstations."""
    run_ami_list(output, show_all)


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
    """Create an AMI from a workstation."""
    run_ami_create(workstation, name, description, no_reboot, wait, timeout)
