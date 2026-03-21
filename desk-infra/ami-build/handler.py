"""Step Functions task Lambda for cloud AMI builds (desk data bucket + DynamoDB)."""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import boto3

# Vendored desk SDK (SAM copies desk/ into artifact)
from desk.ami_recipe import validate_recipe_body
from desk.aws import (
    create_ami,
    create_workstation,
    get_ami_state,
    get_command_invocation,
    get_latest_ubuntu_ami,
    send_ssm_command,
    terminate_instance,
    wait_for_ssm_ready,
)

RECIPES_TABLE = os.environ.get("DESK_AMI_RECIPES_TABLE", "")
BUILDS_TABLE = os.environ.get("DESK_AMI_BUILDS_TABLE", "")
DATA_BUCKET = os.environ.get("DESK_DATA_BUCKET", "")


def _ddb():
    return boto3.client("dynamodb")


def _s3():
    return boto3.client("s3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_ami_name(ami_name: str) -> str:
    base = ami_name.lower().strip()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    base = base.strip("-") or "ami-builder"
    return base[:240]


def _builder_instance_name(ami_name: str) -> str:
    return f"{_slug_ami_name(ami_name)}-{secrets.token_hex(4)}"


def _versioned_ami_name(ami_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = ami_name.strip()
    max_base = 128 - len(timestamp) - 1
    if len(base) > max_base:
        base = base[:max_base]
    return f"{base}-{timestamp}"


def _ddb_attr(v: Any) -> dict[str, Any]:
    if isinstance(v, (dict, list)):
        return {"S": json.dumps(v)}
    if isinstance(v, bool):
        return {"BOOL": v}
    if isinstance(v, int):
        return {"N": str(v)}
    return {"S": str(v)}


def _update_build(build_id: str, **fields: Any) -> None:
    if not BUILDS_TABLE:
        return
    names: dict[str, str] = {}
    values: dict[str, dict] = {}
    sets = []
    i = 0
    for k, v in fields.items():
        if v is None:
            continue
        nk = f"#k{i}"
        vk = f":v{i}"
        names[nk] = k
        values[vk] = _ddb_attr(v)
        sets.append(f"{nk} = {vk}")
        i += 1
    if not sets:
        return
    names["#ua"] = "updated_at"
    values[":ua"] = {"S": _now_iso()}
    sets.append("#ua = :ua")
    _ddb().update_item(
        TableName=BUILDS_TABLE,
        Key={"build_id": {"S": build_id}},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _get_recipe(recipe_id: str) -> dict[str, Any] | None:
    if not RECIPES_TABLE:
        return None
    r = _ddb().get_item(
        TableName=RECIPES_TABLE,
        Key={"recipe_id": {"S": recipe_id}},
        ConsistentRead=True,
    )
    item = r.get("Item")
    if not item:
        return None
    body_raw = item.get("body", {}).get("S", "{}")
    try:
        body = json.loads(body_raw)
    except json.JSONDecodeError:
        body = {}
    return {
        "recipe_id": item["recipe_id"]["S"],
        "name": item.get("name", {}).get("S", ""),
        "body": body if isinstance(body, dict) else {},
    }


def _wait_ssm_command(command_id: str, instance_id: str, *, timeout: int = 7200) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = get_command_invocation(command_id, instance_id)
        if r.status in ("Pending", "InProgress", "Delayed"):
            time.sleep(2)
            continue
        if r.status == "Success":
            code = r.exit_code
            if code not in (None, 0):
                raise RuntimeError(
                    f"Command exited {code}. stderr={r.stderr!s} stdout={r.stdout!s}"
                )
            return
        raise RuntimeError(f"SSM command status={r.status} stderr={r.stderr!s}")
    raise TimeoutError("Timed out waiting for SSM command")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    u = uri.strip()
    if not u.startswith("s3://"):
        raise ValueError("Copy source must be s3://bucket/key")
    rest = u[5:]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid s3 URI (need s3://bucket/key)")
    return parts[0], parts[1]


def _ensure_data_bucket_uri(uri: str) -> tuple[str, str]:
    bucket, key = _parse_s3_uri(uri)
    if bucket != DATA_BUCKET:
        raise ValueError(
            f"Copy source must use the desk data bucket s3://{DATA_BUCKET}/..."
        )
    return bucket, key


def handle_validate_and_load(payload: dict[str, Any]) -> dict[str, Any]:
    build_id = payload["build_id"]
    recipe_id = payload["recipe_id"]
    recipe = _get_recipe(recipe_id)
    if not recipe:
        raise ValueError(f"Recipe not found: {recipe_id}")
    body = recipe["body"]
    normalized = validate_recipe_body(body, cloud=True)
    workstation_name = _builder_instance_name(normalized["ami_name"])
    versioned_ami_name = _versioned_ami_name(normalized["ami_name"])
    _update_build(
        build_id,
        status="running",
        workstation_name=workstation_name,
        recipe_name=recipe.get("name") or "",
        error_message="",
    )
    return {
        **payload,
        "recipe_name": recipe.get("name") or "",
        "ami_name": normalized["ami_name"],
        "instance_type": normalized["instance_type"],
        "steps": normalized["steps"],
        "workstation_name": workstation_name,
        "versioned_ami_name": versioned_ami_name,
    }


def handle_create_builder(ctx: dict[str, Any]) -> dict[str, Any]:
    ws = ctx["workstation_name"]
    itype = ctx["instance_type"]
    ami_id = get_latest_ubuntu_ami()
    instance_id, _ = create_workstation(
        ws,
        itype,
        ami_id=ami_id,
        shutdown_after="24h",
    )
    _update_build(ctx["build_id"], instance_id=instance_id)
    return {**ctx, "instance_id": instance_id}


def handle_wait_for_ssm(ctx: dict[str, Any]) -> dict[str, Any]:
    iid = ctx["instance_id"]
    if not wait_for_ssm_ready(iid, timeout=600):
        raise TimeoutError("Instance did not become SSM-ready within 600s")
    return ctx


def handle_run_step(payload: dict[str, Any]) -> dict[str, Any]:
    step = payload["step"]
    instance_id = payload["instance_id"]
    cmd = step["run"]
    cid = send_ssm_command(instance_id, cmd, timeout_seconds=7200)
    _wait_ssm_command(cid, instance_id, timeout=7200)
    return payload


def handle_copy_step(payload: dict[str, Any]) -> dict[str, Any]:
    step = payload["step"]["copy"]
    instance_id = payload["instance_id"]
    source = step["source"].strip()
    dest = step["dest"].strip()
    recursive = bool(step.get("recursive", False))
    bucket, key = _ensure_data_bucket_uri(source)

    if recursive:
        prefix = key.rstrip("/")
        dest_dir = dest.rstrip("/") + "/"
        script = f"""set -euo pipefail
if ! command -v aws >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y awscli
fi
sudo mkdir -p {dest_dir}
aws s3 sync "s3://{bucket}/{prefix}/" "{dest_dir}" --region "$AWS_REGION"
"""
    else:
        url = _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=3600,
        )
        safe_url = url.replace("'", "'\"'\"'")
        dest_parent = dest.rsplit("/", 1)[0] if "/" in dest else "."
        script = f"""set -euo pipefail
sudo mkdir -p {dest_parent}
curl -fsSL '{safe_url}' -o {dest}
"""

    cid = send_ssm_command(instance_id, script, timeout_seconds=7200)
    _wait_ssm_command(cid, instance_id, timeout=7200)
    return payload


def handle_create_ami(ctx: dict[str, Any]) -> dict[str, Any]:
    instance_id = ctx["instance_id"]
    name = ctx["versioned_ami_name"]
    image_id = create_ami(instance_id, name, description="Desk cloud AMI build", no_reboot=False)
    _update_build(ctx["build_id"], ami_id=image_id, ami_name=name)
    return {**ctx, "image_id": image_id}


def handle_poll_ami(ctx: dict[str, Any]) -> dict[str, Any]:
    image_id = ctx["image_id"]
    state = get_ami_state(image_id)
    if state == "available":
        return {**ctx, "ami_ready": True, "ami_failed": False}
    if state in ("failed", "error", "deregistered"):
        return {**ctx, "ami_ready": True, "ami_failed": True, "ami_state": state}
    return {**ctx, "ami_ready": False, "ami_failed": False, "ami_state": state}


def handle_terminate_builder(ctx: dict[str, Any]) -> dict[str, Any]:
    iid = ctx.get("instance_id")
    if iid:
        try:
            terminate_instance(iid)
        except Exception:
            traceback.print_exc()
    return ctx


def handle_finalize_success(ctx: dict[str, Any]) -> dict[str, Any]:
    _update_build(
        ctx["build_id"],
        status="succeeded",
        ami_id=ctx.get("image_id", ""),
        error_message="",
    )
    return ctx


def handle_cleanup_and_fail(payload: dict[str, Any]) -> dict[str, Any]:
    """Terminate builder if present; mark build failed."""
    build_id = payload.get("build_id", "")
    raw_err = payload.get("error")
    err = payload.get("error_message") or "Unknown error"
    if isinstance(raw_err, dict):
        err = str(raw_err.get("Cause") or raw_err.get("Error") or json.dumps(raw_err))
    elif raw_err is not None:
        err = str(raw_err)
    instance_id = payload.get("instance_id")
    if instance_id:
        try:
            terminate_instance(instance_id)
        except Exception:
            traceback.print_exc()
    if build_id:
        _update_build(build_id, status="failed", error_message=str(err)[:8000])
    return {"ok": True, "build_id": build_id}


ACTIONS: dict[str, Any] = {
    "ValidateAndLoad": handle_validate_and_load,
    "CreateBuilder": handle_create_builder,
    "WaitForSSM": handle_wait_for_ssm,
    "RunStep": handle_run_step,
    "CopyStep": handle_copy_step,
    "CreateAmi": handle_create_ami,
    "PollAmi": handle_poll_ami,
    "TerminateBuilder": handle_terminate_builder,
    "FinalizeSuccess": handle_finalize_success,
    "CleanupAndFail": handle_cleanup_and_fail,
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    action = event.get("action")
    payload = event.get("payload")
    if not action or not isinstance(payload, dict):
        raise ValueError("Invalid event: need action and payload object")
    fn = ACTIONS.get(action)
    if not fn:
        raise ValueError(f"Unknown action: {action}")
    return fn(payload)
