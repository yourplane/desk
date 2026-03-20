"""AWS Cost Explorer helpers for desk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import boto3

from desk.log import get_logger

log = get_logger("costs")

SERVICE_FRIENDLY_NAMES: dict[str, str] = {
    "Amazon Elastic Compute Cloud - Compute": "EC2 Instances",
    "EC2 - Other": "EC2 Other (NAT, EBS, EIPs)",
    "Amazon Virtual Private Cloud": "VPC",
    "Amazon Simple Storage Service": "S3",
    "AWS Lambda": "Lambda",
    "Amazon CloudFront": "CloudFront",
    "Amazon API Gateway": "API Gateway",
    "AWS WAF": "WAF",
    "Amazon Cognito": "Cognito",
    "Amazon CloudWatch": "CloudWatch",
    "AmazonCloudWatch": "CloudWatch",
    "AWS Key Management Service": "KMS",
    "AWS CloudFormation": "CloudFormation",
    "AWS Systems Manager": "Systems Manager",
    "Amazon EC2 Container Registry (ECR)": "ECR",
    "Tax": "Tax",
}

CATEGORY_MAP: dict[str, str] = {
    "EC2 Instances": "EC2 Instances",
    "EC2 Other (NAT, EBS, EIPs)": "EC2 Infrastructure",
    "VPC": "EC2 Infrastructure",
}


def _friendly_name(raw_service: str) -> str:
    return SERVICE_FRIENDLY_NAMES.get(raw_service, raw_service)


def _category(friendly: str) -> str:
    return CATEGORY_MAP.get(friendly, "Other")


@dataclass
class ServiceCost:
    service: str
    amount: float
    category: str


@dataclass
class MonthlyCost:
    month: str
    total: float
    services: list[ServiceCost] = field(default_factory=list)


@dataclass
class DailyCost:
    date: str
    total: float


@dataclass
class CostSummary:
    months: list[MonthlyCost] = field(default_factory=list)
    daily_current_month: list[DailyCost] = field(default_factory=list)


def _parse_results_by_time(results: list[dict]) -> list[dict]:
    """Extract (period_start, service, amount) triples from Cost Explorer results."""
    entries = []
    for result in results:
        period_start = result["TimePeriod"]["Start"]
        for group in result.get("Groups", []):
            service_raw = group["Keys"][0]
            amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
            entries.append({
                "period": period_start,
                "service_raw": service_raw,
                "amount": amount,
            })
        # Some results have no groups but a Total
        if not result.get("Groups") and "Total" in result:
            amount = float(result["Total"]["UnblendedCost"]["Amount"])
            entries.append({
                "period": period_start,
                "service_raw": "Total",
                "amount": amount,
            })
    return entries


def get_cost_summary(
    months: int = 6,
    region: str | None = None,
    profile: str | None = None,
) -> CostSummary:
    """Fetch cost data from AWS Cost Explorer.

    Returns monthly totals with per-service breakdowns for the last ``months``
    months, plus daily totals for the current month.
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ce = session.client("ce", region_name="us-east-1")

    today = date.today()
    first_of_current = today.replace(day=1)

    # Monthly range: first of (months-1) months ago through tomorrow
    start_month = first_of_current - timedelta(days=1)
    for _ in range(months - 1):
        start_month = (start_month.replace(day=1) - timedelta(days=1))
    start_date = start_month.replace(day=1)
    end_date = today + timedelta(days=1)

    log.debug(
        "get_cost_summary monthly range %s to %s", start_date.isoformat(), end_date.isoformat()
    )

    monthly_response = ce.get_cost_and_usage(
        TimePeriod={"Start": start_date.isoformat(), "End": end_date.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )

    entries = _parse_results_by_time(monthly_response.get("ResultsByTime", []))

    months_map: dict[str, MonthlyCost] = {}
    for e in entries:
        period = e["period"][:7]  # "2026-03"
        if period not in months_map:
            months_map[period] = MonthlyCost(month=period, total=0.0)
        mc = months_map[period]
        friendly = _friendly_name(e["service_raw"])
        cat = _category(friendly)
        amt = round(e["amount"], 2)
        if abs(amt) >= 0.005:
            mc.services.append(ServiceCost(service=friendly, amount=amt, category=cat))
            mc.total = round(mc.total + e["amount"], 2)

    for mc in months_map.values():
        mc.services.sort(key=lambda s: s.amount, reverse=True)

    monthly_list = sorted(months_map.values(), key=lambda m: m.month)

    # Daily breakdown for current month
    daily_start = first_of_current.isoformat()
    daily_end = end_date.isoformat()

    log.debug("get_cost_summary daily range %s to %s", daily_start, daily_end)

    daily_response = ce.get_cost_and_usage(
        TimePeriod={"Start": daily_start, "End": daily_end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )

    daily_list: list[DailyCost] = []
    for result in daily_response.get("ResultsByTime", []):
        d = result["TimePeriod"]["Start"]
        amt = float(result.get("Total", {}).get("UnblendedCost", {}).get("Amount", "0"))
        daily_list.append(DailyCost(date=d, total=round(amt, 2)))

    daily_list.sort(key=lambda d: d.date)

    return CostSummary(months=monthly_list, daily_current_month=daily_list)
