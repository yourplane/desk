"""Instance management routes. All EC2 logic lives in desk-sdk."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.aws import (
    clear_shutdown_tag,
    compute_shutdown_at,
    list_workstations,
    parse_duration,
    resolve_workstation,
    set_shutdown_tag,
    start_instance,
    stop_instance,
)
from desk.config import get_default_profile, get_default_region

logger = logging.getLogger(__name__)
router = APIRouter(tags=["instances"])


def _region_profile():
    return get_default_region(), get_default_profile()


class AutoStopBody(BaseModel):
    """Request body for POST /instances/{name}/auto-stop."""

    duration: str | None = None
    clear: bool = False


@router.get("/instances")
def list_instances():
    """List workstation instances (tagged Type=workstation)."""
    region, profile = _region_profile()
    logger.info("list_instances: region=%s profile=%s", region, profile)
    try:
        workstations = list_workstations(region=region, profile=profile)
    except Exception as e:
        logger.exception("list_workstations failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    logger.info("list_instances: returning %d workstations", len(workstations))
    return [
        {
            "instance_id": w.instance_id,
            "name": w.name or "-",
            "state": w.state,
            "shutdown_at": w.shutdown_at,
        }
        for w in workstations
    ]


@router.post("/instances/{name}/start")
def start_instance_by_name(name: str):
    """Start a stopped workstation by name or instance ID."""
    region, profile = _region_profile()
    try:
        instance_id = resolve_workstation(
            name, region=region, profile=profile, states=["stopped"]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    start_instance(instance_id, region=region, profile=profile)
    return {"instance_id": instance_id}


@router.post("/instances/{name}/stop")
def stop_instance_by_name(name: str):
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


@router.post("/instances/{name}/auto-stop")
def set_auto_stop(name: str, body: AutoStopBody):
    """Set or clear the auto-stop time for a workstation. Body: { \"duration\": \"4h\" } or { \"clear\": true }."""
    region, profile = _region_profile()
    try:
        instance_id = resolve_workstation(
            name, region=region, profile=profile
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if body.clear:
        clear_shutdown_tag(instance_id, region=region, profile=profile)
        return {"instance_id": instance_id, "shutdown_cleared": True}
    duration = body.duration or "4h"
    try:
        hours = parse_duration(duration)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    shutdown_at = compute_shutdown_at(hours)
    set_shutdown_tag(instance_id, shutdown_at, region=region, profile=profile)
    return {"instance_id": instance_id, "shutdown_at": shutdown_at}
