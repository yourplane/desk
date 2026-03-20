"""Workstation management routes. All EC2 logic lives in desk-sdk."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.aws import (
    clear_shutdown_tag,
    compute_shutdown_at,
    create_workstation,
    list_workstations,
    parse_duration,
    resolve_workstation,
    set_shutdown_tag,
    start_workstation,
    stop_instance,
    terminate_instance,
)
from desk.config import get_default_profile, get_default_region

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workstations"])


def _region_profile():
    return get_default_region(), get_default_profile()


class CreateWorkstationBody(BaseModel):
    """Request body for POST /workstations."""

    name: str
    instance_type: str = "t3.medium"
    shutdown_after: str = "4h"


class AutoStopBody(BaseModel):
    """Request body for POST /workstations/{name}/auto-stop."""

    duration: str | None = None
    shutdown_at: str | None = None
    clear: bool = False


def _parse_shutdown_at(value: str) -> str:
    """Validate and normalise an ISO 8601 timestamp to ``YYYY-MM-DDTHH:MM:SSZ``."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid shutdown_at timestamp: {value!r}. Use ISO 8601 format.",
        ) from e
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_or_clear_auto_stop(name: str, body: AutoStopBody, *, region: str, profile: str):
    """Shared implementation for auto-stop endpoints (workstations + legacy instances)."""
    if body.duration and body.shutdown_at:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'duration' or 'shutdown_at', not both.",
        )

    try:
        instance_id = resolve_workstation(
            name,
            region=region,
            profile=profile,
            states=["running", "pending", "stopping", "stopped"],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    logger.info(
        "auto-stop request name=%s resolved_instance_id=%s duration=%s shutdown_at=%s clear=%s",
        name,
        instance_id,
        body.duration,
        body.shutdown_at,
        body.clear,
    )
    if body.clear:
        clear_shutdown_tag(instance_id, region=region, profile=profile)
        return {"instance_id": instance_id, "shutdown_cleared": True}

    if body.shutdown_at:
        shutdown_at = _parse_shutdown_at(body.shutdown_at)
        set_shutdown_tag(instance_id, shutdown_at, region=region, profile=profile)
        return {"instance_id": instance_id, "shutdown_at": shutdown_at}

    duration = body.duration or "4h"
    try:
        hours = parse_duration(duration)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    shutdown_at = compute_shutdown_at(hours)
    set_shutdown_tag(instance_id, shutdown_at, region=region, profile=profile)
    return {"instance_id": instance_id, "shutdown_at": shutdown_at}


@router.post("/workstations")
def create_workstation_route(body: CreateWorkstationBody):
    """Create a new workstation instance."""
    region, profile = _region_profile()

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workstation name must not be empty.")

    try:
        instance_id, shutdown_at = create_workstation(
            name,
            body.instance_type,
            shutdown_after=body.shutdown_after,
            region=region,
            profile=profile,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        logger.exception("create workstation failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    logger.info("created workstation name=%s instance_id=%s", name, instance_id)
    return {"instance_id": instance_id, "name": name, "shutdown_at": shutdown_at}


@router.get("/workstations")
def list_workstations_route():
    """List workstations (EC2 instances tagged Type=workstation)."""
    region, profile = _region_profile()
    logger.info("list_workstations: region=%s profile=%s", region, profile)
    try:
        workstations = list_workstations(region=region, profile=profile)
    except Exception as e:
        logger.exception("list_workstations failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    logger.info("list_workstations: returning %d workstations", len(workstations))
    return [
        {
            "instance_id": w.instance_id,
            "name": w.name or "-",
            "state": w.state,
            "shutdown_at": w.shutdown_at,
        }
        for w in workstations
    ]


@router.post("/workstations/{name}/start")
def start_workstation_by_name(name: str):
    """Start a stopped workstation by name or instance ID. Sets auto-stop to 4 hours."""
    region, profile = _region_profile()
    try:
        instance_id = resolve_workstation(
            name, region=region, profile=profile, states=["stopped"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    instance_id, shutdown_at = start_workstation(
        instance_id, shutdown_after="4h", region=region, profile=profile
    )
    return {"instance_id": instance_id, "shutdown_at": shutdown_at}


@router.post("/workstations/{name}/stop")
def stop_workstation_by_name(name: str):
    """Stop a running workstation by name or instance ID."""
    region, profile = _region_profile()
    try:
        instance_id = resolve_workstation(
            name, region=region, profile=profile
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    stop_instance(instance_id, region=region, profile=profile)
    return {"instance_id": instance_id}


@router.post("/workstations/{name}/kill")
def kill_instance_by_name(name: str):
    """Permanently terminate a workstation by name or instance ID."""
    region, profile = _region_profile()
    try:
        instance_id = resolve_workstation(
            name,
            region=region,
            profile=profile,
            states=["pending", "running", "stopping", "stopped"],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    try:
        terminate_instance(instance_id, region=region, profile=profile)
    except Exception as e:
        logger.exception("terminate_instance failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"instance_id": instance_id}


@router.post("/workstations/{name}/auto-stop")
def set_auto_stop(name: str, body: AutoStopBody):
    """Set or clear the auto-stop time for a workstation.

    Body: { "duration": "4h" }, { "shutdown_at": "2025-06-01T17:30:00Z" },
    or { "clear": true }.
    """
    region, profile = _region_profile()
    return _set_or_clear_auto_stop(name, body, region=region, profile=profile)
