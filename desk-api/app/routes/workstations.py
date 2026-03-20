"""Workstation management routes. All EC2 logic lives in desk-sdk."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.aws import (
    clear_shutdown_tag,
    compute_shutdown_at,
    get_desk_vpc_outputs,
    get_latest_ami_by_name_prefix,
    get_latest_ubuntu_ami,
    list_workstations,
    parse_duration,
    resolve_workstation,
    run_workstation,
    set_shutdown_tag,
    start_workstation,
    stop_instance,
    terminate_instance,
)
from desk.config import get_default_ami_prefix, get_default_profile, get_default_region

logger = logging.getLogger(__name__)
router = APIRouter(tags=["workstations"])


def _region_profile():
    return get_default_region(), get_default_profile()


class CreateWorkstationBody(BaseModel):
    """Request body for POST /workstations."""

    name: str
    instance_type: str = "t3.medium"
    shutdown_after: str = "4h"
    stack: str = "desk"


class AutoStopBody(BaseModel):
    """Request body for POST /workstations/{name}/auto-stop."""

    duration: str | None = None
    clear: bool = False


def _set_or_clear_auto_stop(name: str, body: AutoStopBody, *, region: str, profile: str):
    """Shared implementation for auto-stop endpoints (workstations + legacy instances)."""
    try:
        # The frontend only shows the control for running/pending, but instances can
        # transition quickly (e.g. running -> stopping) between render and click.
        # Allow a slightly broader set of states to avoid spurious 404s.
        instance_id = resolve_workstation(
            name,
            region=region,
            profile=profile,
            states=["running", "pending", "stopping", "stopped"],
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    logger.info(
        "auto-stop request name=%s resolved_instance_id=%s duration=%s clear=%s",
        name,
        instance_id,
        body.duration,
        body.clear,
    )
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


@router.post("/workstations")
def create_workstation_route(body: CreateWorkstationBody):
    """Create a new workstation instance."""
    region, profile = _region_profile()

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Workstation name must not be empty.")

    existing = list_workstations(region=region, profile=profile)
    duplicates = [w for w in existing if w.name == name and w.state != "terminated"]
    if duplicates:
        states = ", ".join(f"{w.instance_id} ({w.state})" for w in duplicates)
        raise HTTPException(
            status_code=409,
            detail=f"Workstation named '{name}' already exists: {states}. "
            "Use a different name or terminate the existing workstation first.",
        )

    try:
        vpc_outputs = get_desk_vpc_outputs(
            stack_name=body.stack, region=region, profile=profile
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    ami_prefix = get_default_ami_prefix()
    ami: str | None = None
    if ami_prefix:
        ami = get_latest_ami_by_name_prefix(ami_prefix, region=region, profile=profile)
    if not ami:
        try:
            ami = get_latest_ubuntu_ami(region=region, profile=profile)
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    subnet_id = vpc_outputs.private_subnet_ids[0]

    try:
        instance_id, shutdown_at = run_workstation(
            ami_id=ami,
            instance_type=body.instance_type,
            subnet_id=subnet_id,
            security_group_ids=[vpc_outputs.security_group_id],
            iam_instance_profile_name=vpc_outputs.instance_profile_name,
            name=name,
            shutdown_after=body.shutdown_after,
            key_name=None,
            region=region,
            profile=profile,
        )
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

    Body: { "duration": "4h" } or { "clear": true }.
    """
    region, profile = _region_profile()
    return _set_or_clear_auto_stop(name, body, region=region, profile=profile)
