"""AMI listing routes."""

import logging

from fastapi import APIRouter, HTTPException

from desk.aws import list_amis
from desk.config import get_desk_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["amis"])


@router.get("/amis")
def list_amis_route(q: str | None = None, managed_only: bool = True, public: bool = False):
    """List desk-managed AMIs available for workstation launch."""
    aws = get_desk_settings().aws_settings
    region, profile = aws.region, aws.profile
    logger.info(
        "list_amis: region=%s profile=%s managed_only=%s public=%s q=%s",
        region,
        profile,
        managed_only,
        public,
        q,
    )
    try:
        amis = list_amis(
            region=region,
            profile=profile,
            managed_only=managed_only,
            name_query=q,
            public_only=public,
        )
    except Exception as e:
        logger.exception("list_amis failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return [
        {
            "image_id": a.image_id,
            "name": a.name,
            "state": a.state,
            "creation_date": a.creation_date,
            "source_instance": a.source_instance,
            "build_status": a.build_status,
        }
        for a in amis
    ]
