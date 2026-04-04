"""desk ami - manage AMIs from workstations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import click

from desk.aws import (
    create_ami,
    create_workstation,
    get_ami_state,
    get_desk_copy_bucket,
    get_instance_state,
    get_latest_ubuntu_ami,
    list_amis,
    resolve_workstation,
    terminate_instance,
    wait_for_ami_available,
    wait_for_instance_state,
    wait_for_ssm_ready,
)
from desk_cli import __version__
from desk_cli.commands.run import run_script_on_instance
from desk_cli.commands.scp import scp_transfer
from desk.config import get_desk_settings


AMI_BUILDS_PREFIX = "ami-builds/"
AMI_BUILD_ARCHIVE_PREFIX = "ami-build-archive/"


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
            shutdown_after="0",
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
