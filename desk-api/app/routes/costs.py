"""Cost tracking routes. Queries AWS Cost Explorer via desk-sdk."""

import logging

from fastapi import APIRouter, HTTPException

from desk.config import get_desk_settings
from desk.costs import (
    HOURLY_OPT_IN_SETUP_URL,
    HourlyNotEnabledError,
    get_cost_daily,
    get_cost_months,
    get_cost_summary,
    get_cost_today_utc,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["costs"])


def _region_profile():
    aws = get_desk_settings().aws_settings
    return aws.region, aws.profile


def _serialize_months(months):
    return [
        {
            "month": m.month,
            "total": m.total,
            "services": [
                {"name": s.service, "amount": s.amount, "category": s.category}
                for s in m.services
            ],
        }
        for m in months
    ]


def _serialize_today_utc(detail):
    return {
        "status": "ok",
        "date": detail.date,
        "hourly": [
            {"hour": h.hour, "total": h.total, "status": h.status}
            for h in detail.hourly
        ],
        "spend_so_far": detail.spend_so_far,
        "projected_total": detail.projected_total,
        "projection_available": detail.projection_available,
    }


@router.get("/costs/months")
def get_costs_months():
    """Return monthly cost totals with per-service breakdown."""
    region, profile = _region_profile()
    logger.info("get_costs_months: region=%s profile=%s", region, profile)
    try:
        months = get_cost_months(months=6, region=region, profile=profile)
    except Exception as e:
        logger.exception("get_cost_months failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"months": _serialize_months(months)}


@router.get("/costs/daily")
def get_costs_daily():
    """Return daily totals for the current month."""
    region, profile = _region_profile()
    logger.info("get_costs_daily: region=%s profile=%s", region, profile)
    try:
        daily = get_cost_daily(region=region, profile=profile)
    except Exception as e:
        logger.exception("get_cost_daily failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "daily_current_month": [
            {"date": d.date, "total": d.total}
            for d in daily
        ],
    }


@router.get("/costs/today-utc")
def get_costs_today_utc():
    """Return today's UTC hourly breakdown and projection, or an unavailable state."""
    region, profile = _region_profile()
    logger.info("get_costs_today_utc: region=%s profile=%s", region, profile)
    try:
        detail = get_cost_today_utc(region=region, profile=profile)
    except HourlyNotEnabledError as e:
        logger.info("hourly cost data unavailable: %s", e)
        return {
            "status": "unavailable",
            "reason": "hourly_opt_in_required",
            "message": (
                "Hourly cost data requires enabling hourly granularity in the "
                "payer account's Cost Explorer Settings."
            ),
            "setup_url": HOURLY_OPT_IN_SETUP_URL,
        }
    except Exception as e:
        logger.exception("get_cost_today_utc failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _serialize_today_utc(detail)


@router.get("/costs")
def get_costs():
    """Return monthly + daily + today UTC cost breakdown (legacy aggregate)."""
    region, profile = _region_profile()
    logger.info("get_costs: region=%s profile=%s", region, profile)
    try:
        summary = get_cost_summary(months=6, region=region, profile=profile)
    except Exception as e:
        logger.exception("get_cost_summary failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "months": _serialize_months(summary.months),
        "daily_current_month": [
            {"date": d.date, "total": d.total}
            for d in summary.daily_current_month
        ],
        "today_utc": (
            {
                "date": summary.today_utc.date,
                "hourly": [
                    {"hour": h.hour, "total": h.total, "status": h.status}
                    for h in summary.today_utc.hourly
                ],
                "spend_so_far": summary.today_utc.spend_so_far,
                "projected_total": summary.today_utc.projected_total,
                "projection_available": summary.today_utc.projection_available,
            }
            if summary.today_utc
            else None
        ),
    }
