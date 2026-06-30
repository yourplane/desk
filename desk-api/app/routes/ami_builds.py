"""AMI build routes (list, detail, cancel). Logic lives in desk-sdk."""

import logging

from fastapi import APIRouter, HTTPException, Query

from desk.ami_build import (
    AmiBuildError,
    AmiBuildNotFoundError,
    archive_ami_build,
    list_ami_builds,
    resolve_ami_build_snapshot,
    status_detail,
)
from desk.config import get_desk_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ami-builds"])


def _region_profile():
    aws = get_desk_settings().aws_settings
    return aws.region, aws.profile


@router.get("/ami-builds")
def get_ami_builds(
    archived: bool = Query(False, description="List archived builds instead of active."),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List staged AMI builds with short status summaries (paginated)."""
    region, profile = _region_profile()
    logger.info(
        "get_ami_builds: archived=%s page=%s page_size=%s",
        archived,
        page,
        page_size,
    )
    try:
        return list_ami_builds(
            archived=archived,
            page=page,
            page_size=page_size,
            region=region,
            profile=profile,
        )
    except AmiBuildError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("list_ami_builds failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ami-builds/{build_id}")
def get_ami_build_detail(
    build_id: str,
    archived: bool = Query(False, description="Look up build under archive prefix."),
    verbose: bool = Query(
        False,
        description="Include SSM script and stdout/stderr for active or failed step.",
    ),
):
    """Full pipeline status for one AMI build."""
    region, profile = _region_profile()
    logger.info("get_ami_build_detail: build_id=%s archived=%s", build_id, archived)
    try:
        snap = resolve_ami_build_snapshot(
            build_id, archived=archived, region=region, profile=profile
        )
        return status_detail(snap, verbose=verbose, region=region, profile=profile)
    except AmiBuildNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AmiBuildError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("get_ami_build_detail failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/ami-builds/{build_id}/cancel")
def cancel_ami_build(build_id: str):
    """Archive an active AMI build in S3 (does not terminate EC2 instances)."""
    region, profile = _region_profile()
    logger.info("cancel_ami_build: build_id=%s", build_id)
    try:
        archive_ami_build(build_id, region=region, profile=profile)
    except AmiBuildNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AmiBuildError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("cancel_ami_build failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"build_id": build_id, "archived": True}
