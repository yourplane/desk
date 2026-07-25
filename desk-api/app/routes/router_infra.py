"""Router infra stack wake/sleep and status routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.config import get_desk_settings
from desk.router_infra import (
    get_router_infra_status,
    is_router_instance_ops_enabled,
    sleep_router_infra,
    wake_router_infra,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["router-infra"])


def _region_profile():
    aws = get_desk_settings().aws_settings
    return aws.region, aws.profile


def _status_payload(status) -> dict:
    return {
        "phase": status.phase,
        "base_stack_status": status.base_stack_status,
        "active_stack_status": status.active_stack_status,
        "asg_name": status.asg_name,
        "asg_desired": status.asg_desired,
        "asg_in_service": status.asg_in_service,
        "target_health": status.target_health,
        "demand": status.demand,
        "active_stack_present": status.active_stack_present,
        "instance_ops_enabled": status.active_stack_present
        and status.phase not in ("sleeping", "unavailable"),
        "messages": status.messages,
    }


@router.get("/router-infra/status")
def router_infra_status_route():
    """Pollable routing-infra health (phase, stacks, ASG, ALB targets)."""
    region, profile = _region_profile()
    try:
        status = get_router_infra_status(region=region, profile=profile)
    except Exception as e:
        logger.exception("get_router_infra_status failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    payload = _status_payload(status)
    if status.phase != "unavailable":
        try:
            payload["instance_ops_enabled"] = is_router_instance_ops_enabled(
                region=region, profile=profile
            )
        except Exception:
            logger.exception("is_router_instance_ops_enabled failed")
    return payload


@router.post("/router-infra/wake")
def router_infra_wake_route():
    """Launch desk-router-active and scale router ASG (fire-and-forget)."""
    region, profile = _region_profile()
    try:
        result = wake_router_infra(region=region, profile=profile)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("wake_router_infra failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


class RouterInfraSleepBody(BaseModel):
    force: bool = False


@router.post("/router-infra/sleep")
def router_infra_sleep_route(body: RouterInfraSleepBody | None = None):
    """Shut down desk-router-active and scale router ASG to zero."""
    region, profile = _region_profile()
    force = body.force if body else False
    try:
        result = sleep_router_infra(region=region, profile=profile, force=force)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        logger.exception("sleep_router_infra failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result
