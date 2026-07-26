"""Router infra idle/wake orchestration (desk-router base + desk-router-active stacks)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

import boto3
from botocore.exceptions import ClientError

from desk.aws import list_workstations
from desk.log import get_logger
from desk.web_routes import list_all_web_routes

log = get_logger("router_infra")

ROUTER_BASE_STACK = "desk-router"
ROUTER_ACTIVE_STACK = "desk-router-active"
ACTIVE_TEMPLATE_S3_KEY = "cf-templates/desk-router-active.yaml"

RouterInfraPhase = Literal["idle", "waking", "active", "sleeping", "error", "unavailable"]

FRIENDLY_LABELS: dict[RouterInfraPhase, str] = {
    "idle": "Stopped",
    "waking": "Starting",
    "active": "Running",
    "sleeping": "Stopping",
    "error": "Error",
    "unavailable": "Unavailable",
}


@dataclass
class DemandSource:
    """Workstation + ports that keep router infra awake."""

    name: str
    ports: list[int]
    state: str


@dataclass
class RouterInfraStatus:
    """Aggregate routing-infra health for API/UI polling."""

    phase: RouterInfraPhase
    base_stack_status: str | None = None
    active_stack_status: str | None = None
    asg_name: str | None = None
    asg_desired: int | None = None
    asg_in_service: int | None = None
    target_health: str | None = None
    demand: bool = False
    active_stack_present: bool = False
    demand_sources: list[DemandSource] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def router_infra_friendly_label(phase: RouterInfraPhase) -> str:
    """User-facing router infra state label."""
    return FRIENDLY_LABELS.get(phase, phase)


def _session(region: str | None, profile: str | None):
    return boto3.Session(region_name=region, profile_name=profile)


def _stack_status(cf: Any, name: str) -> str | None:
    try:
        resp = cf.describe_stacks(StackName=name)
        stacks = resp.get("Stacks", [])
        if not stacks:
            return None
        return stacks[0].get("StackStatus")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            return None
        raise


def _stack_outputs(cf: Any, name: str) -> dict[str, str]:
    try:
        resp = cf.describe_stacks(StackName=name)
        stacks = resp.get("Stacks", [])
        if not stacks:
            return {}
        return {
            o["OutputKey"]: o["OutputValue"]
            for o in stacks[0].get("Outputs", [])
            if "OutputKey" in o and "OutputValue" in o
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            return {}
        raise


def _stack_params(cf: Any, name: str) -> dict[str, str]:
    try:
        resp = cf.describe_stacks(StackName=name)
        stacks = resp.get("Stacks", [])
        if not stacks:
            return {}
        return {
            p["ParameterKey"]: p["ParameterValue"]
            for p in stacks[0].get("Parameters", [])
            if "ParameterKey" in p and "ParameterValue" in p
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            return {}
        raise


def _is_in_progress(status: str | None) -> bool:
    if not status:
        return False
    return status.endswith("_IN_PROGRESS")


def _is_failed(status: str | None) -> bool:
    if not status:
        return False
    return "FAILED" in status or status.endswith("_ROLLBACK_COMPLETE")


def _active_stack_needs_delete(status: str | None) -> bool:
    """True when desk-router-active exists but is not usable (failed create/update)."""
    if not status or status in ("DELETE_COMPLETE", "DELETE_IN_PROGRESS"):
        return False
    if status in ("DELETE_FAILED",):
        return True
    if "ROLLBACK" in status or status in ("CREATE_FAILED", "UPDATE_FAILED"):
        return True
    return False


def _active_stack_usable(status: str | None) -> bool:
    if not status:
        return False
    return status.endswith("_COMPLETE") and "ROLLBACK" not in status and "DELETE" not in status


def get_router_infra_demand_sources(
    *,
    region: str | None = None,
    profile: str | None = None,
    prune_stale: bool = True,
) -> list[DemandSource]:
    """Pending/running workstations that have S3 web-route ports."""
    if prune_stale:
        from desk.web_routes import prune_stale_web_routes

        prune_stale_web_routes(region=region, profile=profile)

    routes = list_all_web_routes()
    if not routes:
        return []

    workstations = list_workstations(
        region=region,
        profile=profile,
        states=["pending", "running"],
    )
    ws_by_name = {w.name: w for w in workstations if w.name}
    sources: list[DemandSource] = []
    for name, ports in routes.items():
        if not ports:
            continue
        ws = ws_by_name.get(name)
        if ws is not None:
            sources.append(DemandSource(name=name, ports=list(ports), state=ws.state))
    return sources


def router_infra_demand_exists(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> bool:
    """True when any pending/running workstation has S3 web-route ports."""
    return bool(get_router_infra_demand_sources(region=region, profile=profile))


def _get_asg_info(
    asg_name: str,
    *,
    session: Any,
) -> tuple[int | None, int | None]:
    asg = session.client("autoscaling")
    try:
        resp = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        groups = resp.get("AutoScalingGroups", [])
        if not groups:
            return None, None
        g = groups[0]
        return g.get("DesiredCapacity"), sum(
            1 for i in g.get("Instances", []) if i.get("LifecycleState") == "InService"
        )
    except ClientError:
        return None, None


def _get_target_health(
    target_group_arn: str | None,
    *,
    session: Any,
) -> str | None:
    if not target_group_arn:
        return None
    elb = session.client("elbv2")
    try:
        resp = elb.describe_target_health(TargetGroupArn=target_group_arn)
        targets = resp.get("TargetHealthDescriptions", [])
        if not targets:
            return "no_targets"
        states = [t.get("TargetHealth", {}).get("State", "unknown") for t in targets]
        if all(s == "healthy" for s in states):
            return "healthy"
        if any(s in ("initial", "draining") for s in states):
            return "initial"
        if any(s == "unhealthy" for s in states):
            return "unhealthy"
        return states[0] if states else "unknown"
    except ClientError:
        return None


def get_router_infra_status(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> RouterInfraStatus:
    """Derive routing-infra phase and component health."""
    session = _session(region, profile)
    cf = session.client("cloudformation")

    base_status = _stack_status(cf, ROUTER_BASE_STACK)
    if base_status is None:
        return RouterInfraStatus(
            phase="unavailable",
            messages=["desk-router base stack is not deployed."],
        )

    active_status = _stack_status(cf, ROUTER_ACTIVE_STACK)
    active_present = active_status is not None and active_status != "DELETE_COMPLETE"
    base_outputs = _stack_outputs(cf, ROUTER_BASE_STACK)
    base_params = _stack_params(cf, ROUTER_BASE_STACK)
    active_outputs = _stack_outputs(cf, ROUTER_ACTIVE_STACK) if active_present else {}

    asg_name = base_outputs.get("RouterAsgName")
    asg_desired, asg_in_service = (
        _get_asg_info(asg_name, session=session) if asg_name else (None, None)
    )
    tg_arn = (
        active_outputs.get("RouterTargetGroupArn")
        or base_params.get("ActiveTargetGroupArn")
        or None
    )
    target_health = _get_target_health(tg_arn, session=session) if tg_arn else None
    demand_sources = get_router_infra_demand_sources(region=region, profile=profile)
    demand = bool(demand_sources)

    messages: list[str] = []
    if _is_failed(base_status) or _is_failed(active_status):
        phase: RouterInfraPhase = "error"
        if _is_failed(base_status):
            messages.append(f"desk-router stack status: {base_status}")
        if _is_failed(active_status):
            messages.append(f"desk-router-active stack status: {active_status}")
    elif _is_in_progress(active_status) and "DELETE" in (active_status or ""):
        phase = "sleeping"
    elif _is_in_progress(active_status) or (
        _is_in_progress(base_status) and base_params.get("ActiveAlbArn")
    ):
        phase = "waking"
    elif not active_present and (asg_desired or 0) == 0:
        phase = "idle"
    elif active_present and (asg_desired or 0) == 0 and not _is_in_progress(active_status):
        phase = "sleeping" if _is_in_progress(base_status) else "idle"
    elif active_present and target_health == "healthy" and (asg_in_service or 0) >= 1:
        phase = "active"
    elif not active_present and (asg_desired or 0) > 0:
        phase = "sleeping"
    elif active_present or (asg_desired or 0) > 0:
        phase = "waking"
    else:
        phase = "idle"

    if demand and phase == "idle":
        phase = "waking"

    return RouterInfraStatus(
        phase=phase,
        base_stack_status=base_status,
        active_stack_status=active_status,
        asg_name=asg_name,
        asg_desired=asg_desired,
        asg_in_service=asg_in_service,
        target_health=target_health,
        demand=demand,
        active_stack_present=active_present,
        demand_sources=demand_sources,
        messages=messages,
    )


def _get_data_bucket(region: str | None, profile: str | None) -> str:
    bucket = os.environ.get("DESK_DATA_BUCKET")
    if bucket:
        return bucket
    from desk.aws import get_desk_data_bucket

    return get_desk_data_bucket(region=region, profile=profile)


def _active_template_url(session: Any) -> str:
    bucket = _get_data_bucket(session.region_name, session.profile_name)
    region = session.region_name or "us-east-1"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{ACTIVE_TEMPLATE_S3_KEY}"


def _update_base_active_params(
    *,
    cf: Any,
    active_outputs: dict[str, str],
    desired_capacity: int,
    enable_web_router_cf: bool,
    clear_alb: bool = False,
) -> None:
    params = _stack_params(cf, ROUTER_BASE_STACK)
    overrides = []
    for key, value in params.items():
        overrides.append({"ParameterKey": key, "ParameterValue": value})

    def _set(key: str, value: str) -> None:
        for p in overrides:
            if p["ParameterKey"] == key:
                p["ParameterValue"] = value
                return
        overrides.append({"ParameterKey": key, "ParameterValue": value})

    if desired_capacity > 0 and active_outputs:
        _set("ActiveAlbArn", active_outputs.get("RouterAlbArn", ""))
        _set("ActiveAlbDnsName", active_outputs.get("RouterAlbDnsName", ""))
        _set("ActiveAlbHostedZoneId", active_outputs.get("RouterAlbHostedZoneId", ""))
        _set("ActiveTargetGroupArn", active_outputs.get("RouterTargetGroupArn", ""))
    else:
        _set("ActiveTargetGroupArn", "")
        if clear_alb:
            _set("ActiveAlbArn", "")
            _set("ActiveAlbDnsName", "")
            _set("ActiveAlbHostedZoneId", "")

    _set("RouterDesiredCapacity", str(desired_capacity))

    cf.update_stack(
        StackName=ROUTER_BASE_STACK,
        UsePreviousTemplate=True,
        Parameters=overrides,
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )
    log.info(
        "update_stack %s desired=%d enable_cf=%s",
        ROUTER_BASE_STACK,
        desired_capacity,
        enable_web_router_cf,
    )


def wake_router_infra(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Create desk-router-active (if needed) and scale router ASG up. Fire-and-forget."""
    session = _session(region, profile)
    cf = session.client("cloudformation")

    base_status = _stack_status(cf, ROUTER_BASE_STACK)
    if base_status is None:
        raise RuntimeError("desk-router base stack is not deployed.")

    base_outputs = _stack_outputs(cf, ROUTER_BASE_STACK)
    base_params = _stack_params(cf, ROUTER_BASE_STACK)
    router_sg = base_outputs.get("RouterSecurityGroupId")
    if not router_sg:
        raise RuntimeError("desk-router stack missing RouterSecurityGroupId output.")

    cf_prefix = base_params.get("CloudFrontVpcOriginPrefixListId", "")
    if not cf_prefix:
        raise RuntimeError("desk-router stack missing CloudFrontVpcOriginPrefixListId parameter.")

    active_status = _stack_status(cf, ROUTER_ACTIVE_STACK)
    if _active_stack_needs_delete(active_status):
        cf.delete_stack(StackName=ROUTER_ACTIVE_STACK)
        log.info("delete_stack %s (status=%s) before recreate", ROUTER_ACTIVE_STACK, active_status)
        return {"step": "delete_failed_active_stack", "stack": ROUTER_ACTIVE_STACK}

    if active_status is None or active_status == "DELETE_COMPLETE":
        template_url = _active_template_url(session)
        cf.create_stack(
            StackName=ROUTER_ACTIVE_STACK,
            TemplateURL=template_url,
            Parameters=[
                {"ParameterKey": "CloudFrontVpcOriginPrefixListId", "ParameterValue": cf_prefix},
                {"ParameterKey": "RouterSecurityGroupId", "ParameterValue": router_sg},
            ],
            Capabilities=["CAPABILITY_NAMED_IAM"],
            Tags=[{"Key": "desk:managed", "Value": "true"}],
        )
        log.info("create_stack %s started", ROUTER_ACTIVE_STACK)
        return {"step": "create_active_stack", "stack": ROUTER_ACTIVE_STACK}

    if _is_in_progress(active_status):
        log.info("desk-router-active already %s", active_status)
        return {"step": "already_in_progress", "stack_status": active_status}

    if not _active_stack_usable(active_status):
        raise RuntimeError(f"desk-router-active stack status {active_status!r} is not usable.")

    active_outputs = _stack_outputs(cf, ROUTER_ACTIVE_STACK)
    enable_cf = base_params.get("EnableWebRouterCloudFront", "false") == "true"
    _update_base_active_params(
        cf=cf,
        active_outputs=active_outputs,
        desired_capacity=1,
        enable_web_router_cf=enable_cf,
    )
    return {"step": "update_base_wake", "stack": ROUTER_BASE_STACK}


def sleep_router_infra(
    *,
    region: str | None = None,
    profile: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Scale ASG to zero and delete desk-router-active. Fire-and-forget."""
    session = _session(region, profile)
    cf = session.client("cloudformation")

    base_status = _stack_status(cf, ROUTER_BASE_STACK)
    if base_status is None:
        raise RuntimeError("desk-router base stack is not deployed.")

    if not force and router_infra_demand_exists(region=region, profile=profile):
        raise ValueError(
            "Router infra is still needed: pending/running workstations have web-route ports. "
            "Use force=true to shut down anyway."
        )

    base_params = _stack_params(cf, ROUTER_BASE_STACK)
    enable_cf = base_params.get("EnableWebRouterCloudFront", "false") == "true"

    # Scale down base stack first (detach TG, desired=0); keep stale ALB refs for CloudFront
    _update_base_active_params(
        cf=cf,
        active_outputs={},
        desired_capacity=0,
        enable_web_router_cf=enable_cf,
        clear_alb=False,
    )

    active_status = _stack_status(cf, ROUTER_ACTIVE_STACK)
    if active_status and active_status not in ("DELETE_COMPLETE", "DELETE_FAILED"):
        cf.delete_stack(StackName=ROUTER_ACTIVE_STACK)
        log.info("delete_stack %s started", ROUTER_ACTIVE_STACK)
        return {"step": "sleep", "deleted_active_stack": True}

    if active_status == "DELETE_FAILED":
        cf.delete_stack(StackName=ROUTER_ACTIVE_STACK)
        log.info("retry delete_stack %s after DELETE_FAILED", ROUTER_ACTIVE_STACK)
        return {"step": "sleep", "deleted_active_stack": True, "retry": True}

    return {"step": "sleep", "deleted_active_stack": False}


def ensure_router_up(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> None:
    """Eager wake when demand may exist. Swallows errors (reaper catch-all)."""
    if not router_infra_demand_exists(region=region, profile=profile):
        return
    status = get_router_infra_status(region=region, profile=profile)
    if status.phase == "active":
        return
    if status.phase == "unavailable":
        log.warning("ensure_router_up: base stack unavailable")
        return
    if status.phase == "error":
        log.warning("ensure_router_up: router infra in error state; attempting wake")
    try:
        wake_router_infra(region=region, profile=profile)
    except Exception:
        log.exception("ensure_router_up failed")


def reconcile_router_infra(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Reaper catch-all: wake when demand exists, sleep when idle."""
    demand = router_infra_demand_exists(region=region, profile=profile)
    status = get_router_infra_status(region=region, profile=profile)

    if status.phase == "unavailable":
        return {"action": "skip", "reason": "base stack unavailable"}

    if demand:
        if status.phase in ("idle", "error") or (
            status.phase == "waking" and not status.active_stack_present
        ):
            result = wake_router_infra(region=region, profile=profile)
            return {"action": "wake", **result}
        if status.active_stack_present and status.phase == "waking":
            # Active stack ready — ensure base is wired up
            session = _session(region, profile)
            cf = session.client("cloudformation")
            active_status = status.active_stack_status
            if active_status and _active_stack_usable(active_status):
                base_params = _stack_params(cf, ROUTER_BASE_STACK)
                if not base_params.get("ActiveAlbArn"):
                    active_outputs = _stack_outputs(cf, ROUTER_ACTIVE_STACK)
                    enable_cf = base_params.get("EnableWebRouterCloudFront", "false") == "true"
                    _update_base_active_params(
                        cf=cf,
                        active_outputs=active_outputs,
                        desired_capacity=1,
                        enable_web_router_cf=enable_cf,
                    )
                    return {"action": "complete_wake", "stack": ROUTER_BASE_STACK}
        return {"action": "noop", "phase": status.phase, "demand": True}

    billable_active = status.active_stack_present or (status.asg_desired or 0) > 0
    if not demand and billable_active:
        if _is_in_progress(status.base_stack_status) or _is_in_progress(
            status.active_stack_status or ""
        ):
            return {"action": "noop", "reason": "stack operation in progress"}
        # Defer sleep while Starting (wake in progress); sleep when Running/active.
        if status.phase == "waking":
            return {"action": "noop", "phase": status.phase, "demand": False}
        result = sleep_router_infra(region=region, profile=profile)
        return {"action": "sleep", **result}

    return {"action": "noop", "phase": status.phase, "demand": False}


def router_infra_reconcile_summary(result: dict[str, Any]) -> str:
    """Human-readable summary of reconcile_router_infra() for CLI/UI."""
    action = result.get("action", "unknown")
    if action == "sleep":
        return "Router infra shutdown initiated."
    if action == "wake":
        step = result.get("step", "wake")
        return f"Router infra wake initiated ({step})."
    if action == "complete_wake":
        return "Router infra wake completed (base stack wired)."
    if action == "skip":
        return f"Router infra skipped ({result.get('reason', 'unknown')})."
    if action == "error":
        return f"Router infra error: {result.get('detail', 'unknown')}."
    if action == "noop":
        reason = result.get("reason")
        if reason == "stack operation in progress":
            return "Router infra unchanged (stack operation in progress)."
        if result.get("demand"):
            phase = result.get("phase", "?")
            return f"Router infra kept awake (demand present, {phase})."
        phase = result.get("phase", "?")
        if phase == "waking":
            return "Router infra still starting; sleep deferred until Running or failed."
        return f"Router infra unchanged ({phase})."
    return f"Router infra reconcile: {action}."


def is_router_instance_ops_enabled(
    *,
    region: str | None = None,
    profile: str | None = None,
) -> bool:
    """Instance Start/Stop/Kill are allowed only when the active stack exists."""
    session = _session(region, profile)
    cf = session.client("cloudformation")
    active_status = _stack_status(cf, ROUTER_ACTIVE_STACK)
    if active_status is None or active_status == "DELETE_COMPLETE":
        return False
    if _is_in_progress(active_status) and "DELETE" in active_status:
        return False
    return active_status.endswith("_COMPLETE")
