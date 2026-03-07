"""Lambda handler for AMI build pipeline. Invoked by Step Function with state; performs one phase and returns updated state."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Tag keys for builder instance
TAG_AMI_BUILD_ID = "desk:ami-build-id"
TAG_AMI_BUILD_STATUS = "desk:ami-build-status"
TAG_AMI_BUILD_NAME = "desk:ami-build-name"
TAG_AMI_BUILD_STEP = "desk:ami-build-step"
TAG_AMI_BUILD_COMMAND_ID = "desk:ami-build-command-id"
TAG_AMI_BUILD_IMAGE_ID = "desk:ami-build-image-id"
TAG_AMI_BUILD_ERROR = "desk:ami-build-error"


def _builder_name(ami_name: str) -> str:
    base = ami_name.lower().strip()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-") or "ami-builder"
    base = base[:240]
    return f"{base}-{secrets.token_hex(4)}"


def _get_build_steps(config: dict) -> list[dict]:
    """Return normalized list of steps from config (same logic as desk.commands.ami)."""
    steps = config.get("steps")
    if steps is not None:
        return steps
    out = []
    for cmd in config.get("run_before_copy") or []:
        out.append({"run": cmd})
    for item in config.get("copy") or []:
        out.append({"copy": item})
    for cmd in config.get("run") or []:
        out.append({"run": cmd})
    return out


def handler(event, context):
    """
    Event: state dict with build_id, bucket, region, and optional instance_id, ssm_ready, steps, step_index, etc.
    Returns: updated state. If state.get("done") is True, pipeline is complete.
    """
    state = dict(event)
    build_id = state.get("build_id")
    bucket = state.get("bucket")
    region = state.get("region")
    if not build_id or not bucket or not region:
        raise ValueError("state must contain build_id, bucket, region")

    # Phase 1: validate config and create builder
    if state.get("instance_id") is None:
        from desk.aws import (
            AMI_BUILDS_PREFIX,
            get_desk_vpc_outputs,
            get_latest_ubuntu_ami,
            get_build_config_from_s3,
            run_instance,
            update_instance_build_tags,
        )
        config = get_build_config_from_s3(bucket, build_id, region=region)
        ami_name = config.get("ami_name")
        if not ami_name:
            raise ValueError("config must specify ami_name")
        instance_type = config.get("instance_type", "t3.medium")
        steps = _get_build_steps(config)
        builder_name = _builder_name(ami_name)
        vpc = get_desk_vpc_outputs(stack_name="desk", region=region)
        ubuntu_ami = get_latest_ubuntu_ami(region=region)
        extra_tags = {
            TAG_AMI_BUILD_ID: build_id,
            TAG_AMI_BUILD_STATUS: "pending",
            TAG_AMI_BUILD_NAME: ami_name,
        }
        instance_id = run_instance(
            ami_id=ubuntu_ami,
            instance_type=instance_type,
            subnet_id=vpc.private_subnet_ids[0],
            security_group_ids=[vpc.security_group_id],
            iam_instance_profile_name=vpc.instance_profile_name,
            name=builder_name,
            region=region,
            extra_tags=extra_tags,
        )
        state["instance_id"] = instance_id
        state["ami_name"] = ami_name
        state["steps"] = steps
        state["step_index"] = 0
        state["ssm_ready"] = False
        state["builder_name"] = builder_name
        return state

    # Phase 2: wait for SSM
    if not state.get("ssm_ready"):
        from desk.aws import is_ssm_ready, update_instance_build_tags
        instance_id = state["instance_id"]
        if is_ssm_ready(instance_id, region=region):
            state["ssm_ready"] = True
            update_instance_build_tags(instance_id, status="steps", region=region)
        return state

    # Phase 3: run steps
    steps = state["steps"]
    step_index = state.get("step_index", 0)
    if step_index < len(steps):
        from desk.aws import (
            AMI_BUILDS_PREFIX,
            get_command_invocation,
            send_ssm_command,
            update_instance_build_tags,
            wait_for_ssm_command,
            run_ssm_s3_copy,
        )
        instance_id = state["instance_id"]
        step = steps[step_index]
        step_label = f"step-{step_index + 1}-of-{len(steps)}"
        update_instance_build_tags(instance_id, step=step_label, region=region)
        if "run" in step:
            cmd = step["run"]
            command_id = send_ssm_command(instance_id, cmd, region=region, timeout_seconds=3600)
            update_instance_build_tags(instance_id, command_id=command_id, region=region)
            result = wait_for_ssm_command(command_id, instance_id, region=region, timeout=3600)
            if result.status != "Success" or (result.exit_code is not None and result.exit_code != 0):
                update_instance_build_tags(
                    instance_id,
                    status="failed",
                    error=result.stderr or result.stdout or f"exit_code={result.exit_code}",
                    region=region,
                )
                state["done"] = True
                state["failed"] = True
                return state
        else:
            item = step["copy"]
            src_key = item["source"]
            dest = item["dest"]
            recursive = item.get("recursive", False)
            s3_uri = f"s3://{bucket}/{AMI_BUILDS_PREFIX}/{build_id}/artifacts/{src_key}"
            command_id = run_ssm_s3_copy(
                instance_id, s3_uri, dest, recursive=recursive, region=region, timeout_seconds=600
            )
            update_instance_build_tags(instance_id, command_id=command_id, region=region)
            result = wait_for_ssm_command(command_id, instance_id, region=region, timeout=900)
            if result.status != "Success" or (result.exit_code is not None and result.exit_code != 0):
                update_instance_build_tags(
                    instance_id,
                    status="failed",
                    error=result.stderr or result.stdout or "copy failed",
                    region=region,
                )
                state["done"] = True
                state["failed"] = True
                return state
        state["step_index"] = step_index + 1
        return state

    # Phase 4: create AMI
    if state.get("image_id") is None and not state.get("failed"):
        from desk.aws import (
            create_ami,
            update_instance_build_tags,
            terminate_instance,
        )
        from datetime import datetime
        instance_id = state["instance_id"]
        ami_name = state["ami_name"]
        versioned_name = f"{ami_name}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        update_instance_build_tags(instance_id, status="ami-create", region=region)
        try:
            image_id = create_ami(
                instance_id=instance_id,
                name=versioned_name,
                region=region,
            )
            update_instance_build_tags(
                instance_id,
                status="done",
                image_id=image_id,
                region=region,
            )
            state["image_id"] = image_id
        except Exception as e:
            update_instance_build_tags(
                instance_id,
                status="failed",
                error=str(e)[:500],
                region=region,
            )
            state["done"] = True
            state["failed"] = True
            return state

    # Phase 5: terminate builder
    if state.get("image_id") and not state.get("terminated"):
        from desk.aws import terminate_instance
        terminate_instance(state["instance_id"], region=region)
        state["terminated"] = True
        state["done"] = True
        return state

    if state.get("failed"):
        state["done"] = True
    return state
