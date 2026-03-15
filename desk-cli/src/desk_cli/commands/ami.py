"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

import click

from desk.aws import (
    create_ami,
    get_ami_state,
    get_instance_state,
    get_latest_ubuntu_ami,
    is_ssm_ready,
    list_amis,
    resolve_workstation,
    stop_instance,
    terminate_instance,
    wait_for_ami_available,
    wait_for_instance_state,
    wait_for_ssm_ready,
)
from desk.config import get_default_profile, get_default_region


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


def _run_desk(
    args: list[str],
    region: str | None,
    profile: str | None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run desk CLI with given args and env."""
    cmd = [sys.executable, "-m", "desk.cli"] + args
    run_env = os.environ.copy()
    if region:
        run_env["AWS_REGION"] = region
    if profile:
        run_env["AWS_PROFILE"] = profile
    if env:
        run_env.update(env)
    return subprocess.run(
        cmd,
        env=run_env,
        check=False,
        capture_output=False,
    )


@ami_group.command("list")
@click.option(
    "--region",
    "-r",
    default=None,
    envvar="AWS_REGION",
    help="AWS region.",
)
@click.option(
    "--profile",
    "-p",
    default=None,
    envvar="AWS_PROFILE",
    help="AWS profile.",
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
    "--all",
    "show_all",
    is_flag=True,
    help="Show all owned AMIs, not only desk-created ones.",
)
def ami_list(
    region: str | None,
    profile: str | None,
    output: str,
    show_all: bool,
) -> None:
    """List AMIs created from desk workstations.

    By default shows only AMIs created with 'desk ami create'. Use --all to show
    all AMIs you own in this region.
    """
    region = region or get_default_region()
    profile = profile or get_default_profile()

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


@ami_group.command("build")
@click.argument(
    "config_file",
    type=click.Path(exists=True),
)
@click.option(
    "--region",
    "-r",
    default=None,
    envvar="AWS_REGION",
    help="AWS region.",
)
@click.option(
    "--profile",
    "-p",
    default=None,
    envvar="AWS_PROFILE",
    help="AWS profile.",
)
@click.option(
    "--no-wait",
    is_flag=True,
    help="Do not wait for AMI to become available after creation.",
)
def ami_build(
    config_file: str,
    region: str | None,
    profile: str | None,
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
    region = region or get_default_region()
    profile = profile or get_default_profile()

    config = _load_build_config(config_file)
    if "base_ami" in config:
        raise click.ClickException(
            "Builder always uses latest Ubuntu 24.04; 'base_ami' is not allowed in recipes."
        )
    instance_type = config.get("instance_type", "t3.medium")
    steps = _get_build_steps(config)
    ami_name = config.get("ami_name")
    if not ami_name:
        raise click.ClickException("Config must specify 'ami_name'.")
    if "workstation_name" in config:
        raise click.ClickException(
            "Config must not specify 'workstation_name'; it is auto-generated from ami_name."
        )
    if "key" in config:
        raise click.ClickException("Config must not specify 'key'.")
    workstation_name = _builder_name(ami_name)

    click.echo(f"Building AMI from config: {config_file}")
    click.echo(f"  Workstation: {workstation_name}")
    click.echo()

    # 1. Create instance from Ubuntu (never use config default/ami_prefix for builder)
    builder_ami = get_latest_ubuntu_ami(region=region, profile=profile)
    create_args = [
        "create",
        workstation_name,
        "--instance-type", instance_type,
        "--ami", builder_ami,
    ]
    # Clear ami prefix so create never falls back to config default
    create_env = {"DESK_AMI_PREFIX": ""}
    click.echo("Step 1/4: Creating builder instance...")
    result = _run_desk(create_args, region, profile, env=create_env)
    if result.returncode != 0:
        raise click.ClickException("desk create failed.")

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
            run_args = [
                "run",
                "--follow",
                "--no-wait",
                workstation_name,
                run_cmd,
            ]
            result = _run_desk(run_args, region, profile)
            if result.returncode != 0:
                raise click.ClickException(f"desk run failed for: {run_cmd}")
        else:
            item = step["copy"]
            src = item["source"]
            dest = item["dest"]
            recursive = item.get("recursive", False)
            if not os.path.isabs(src):
                src = os.path.normpath(os.path.join(config_dir, src))
            click.echo(f"Step 3/4: Copy ({i + 1}/{len(steps)}): {src} -> {workstation_name}:{dest}")
            scp_args = [
                "scp",
                "--no-wait",
                workstation_name,
                src,
                f"{workstation_name}:{dest}",
            ]
            if recursive:
                scp_args.insert(2, "-r")  # after --no-wait, before positionals
            result = _run_desk(scp_args, region, profile, env={"PWD": config_dir})
            if result.returncode != 0:
                raise click.ClickException(f"desk scp failed for {src} -> {dest}.")

    click.echo()

    # 4. Create AMI
    versioned_name = _versioned_ami_name(ami_name)
    click.echo("Step 4/4: Creating AMI from builder...")
    click.echo(f"  AMI name: {versioned_name}")
    ami_create_args = [
        "ami", "create",
        workstation_name,
        "--name", versioned_name,
        "--no-wait" if no_wait else "--wait",
    ]
    result = _run_desk(ami_create_args, region, profile)
    if result.returncode != 0:
        raise click.ClickException("desk ami create failed.")

    click.echo()
    click.echo("Terminating builder instance...")
    terminate_instance(instance_id, region=region, profile=profile)
    click.secho("AMI build complete.", fg="green", bold=True)


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
@click.option(
    "--region",
    "-r",
    default=None,
    envvar="AWS_REGION",
    help="AWS region.",
)
@click.option(
    "--profile",
    "-p",
    default=None,
    envvar="AWS_PROFILE",
    help="AWS profile.",
)
def ami_create(
    workstation: str,
    name: str | None,
    description: str | None,
    no_reboot: bool,
    wait: bool,
    timeout: int,
    region: str | None,
    profile: str | None,
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
    region = region or get_default_region()
    profile = profile or get_default_profile()

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
