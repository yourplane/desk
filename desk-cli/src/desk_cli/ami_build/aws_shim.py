"""AWS / boto3 entry points for AMI build code (tests patch symbols on this module)."""

from __future__ import annotations

import boto3
from desk.aws import (
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
    terminate_instance,
    wait_for_ami_available,
    wait_for_instance_state,
)

__all__ = [
    "boto3",
    "create_ami",
    "create_workstation",
    "generate_presigned_get_object_url",
    "get_ami_state",
    "get_command_invocation",
    "get_desk_copy_bucket",
    "get_instance_state",
    "get_latest_ubuntu_ami",
    "get_ssm_command",
    "is_ssm_ready",
    "list_amis",
    "list_command_invocations_for_instance",
    "resolve_workstation",
    "send_ssm_command",
    "terminate_instance",
    "wait_for_ami_available",
    "wait_for_instance_state",
]
