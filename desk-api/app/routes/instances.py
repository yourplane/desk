"""Instance management routes. All EC2 logic lives in desk-sdk."""

import logging

from fastapi import APIRouter, HTTPException

from desk.aws import (
    list_workstations,
    resolve_workstation,
    start_instance,
    stop_instance,
    terminate_instance,
)
from desk.config import get_default_profile, get_default_region

logger = logging.getLogger(__name__)
router = APIRouter(tags=["instances"])


def _region_profile():
    return get_default_region(), get_default_profile()


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


@router.post("/instances/{name}/kill")
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
