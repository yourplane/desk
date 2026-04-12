"""HTTP API for web route port registry (S3-backed; no actual proxying)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.aws import resolve_workstation
from desk.web_routes import (
    add_port,
    get_ports,
    list_all_web_routes,
    remove_port,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["web-routes"])

_WORKSTATION_STATES = ["pending", "running", "stopping", "stopped"]


def _region_profile():
    from desk.config import get_desk_settings

    aws = get_desk_settings().aws_settings
    return aws.region, aws.profile


def _ensure_workstation_exists(name: str) -> str:
    region, profile = _region_profile()
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Workstation name must not be empty.")
    try:
        resolve_workstation(
            normalized,
            region=region,
            profile=profile,
            states=_WORKSTATION_STATES,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return normalized


class AddWebRouteBody(BaseModel):
    port: int


@router.get("/web-routes")
def list_all_web_routes_route():
    """Return every workstation's registered ports (one S3 read)."""
    try:
        routes = list_all_web_routes()
    except Exception as e:
        logger.exception("list_all_web_routes failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"routes": routes}


@router.get("/workstations/{name}/web-routes")
def get_workstation_web_routes(name: str):
    """Return registered ports for a workstation."""
    key = _ensure_workstation_exists(name)
    try:
        ports = get_ports(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_ports failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"name": key, "ports": ports}


@router.post("/workstations/{name}/web-routes")
def add_workstation_web_route(name: str, body: AddWebRouteBody):
    """Register a TCP port for routing (stored in S3 only)."""
    key = _ensure_workstation_exists(name)
    try:
        ports = add_port(key, body.port)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("add_port failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"name": key, "ports": ports}


@router.delete("/workstations/{name}/web-routes/{port}")
def remove_workstation_web_route(name: str, port: int):
    """Remove a registered port. Returns 404 if the port was not registered."""
    key = _ensure_workstation_exists(name)
    try:
        ports = remove_port(key, port)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("remove_port failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"name": key, "ports": ports}
