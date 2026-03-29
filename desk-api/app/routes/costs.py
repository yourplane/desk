"""Cost tracking routes. Queries AWS Cost Explorer via desk-sdk."""

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from desk.config import get_desk_settings
from desk.costs import get_cost_summary

logger = logging.getLogger(__name__)
router = APIRouter(tags=["costs"])


def _region_profile():
    aws = get_desk_settings().aws_settings
    return aws.region, aws.profile


@router.get("/costs")
def get_costs():
    """Return monthly + daily cost breakdown from AWS Cost Explorer."""
    region, profile = _region_profile()
    logger.info("get_costs: region=%s profile=%s", region, profile)
    try:
        summary = get_cost_summary(months=6, region=region, profile=profile)
    except Exception as e:
        logger.exception("get_cost_summary failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "months": [
            {
                "month": m.month,
                "total": m.total,
                "services": [
                    {"name": s.service, "amount": s.amount, "category": s.category}
                    for s in m.services
                ],
            }
            for m in summary.months
        ],
        "daily_current_month": [
            {"date": d.date, "total": d.total}
            for d in summary.daily_current_month
        ],
    }
