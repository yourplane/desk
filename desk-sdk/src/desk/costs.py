"""AWS Cost Explorer helpers for desk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

from desk.log import get_logger

log = get_logger("costs")

HOURLY_OPT_IN_SETUP_URL = (
    "https://console.aws.amazon.com/cost-management/home#/settings"
)


class HourlyNotEnabledError(Exception):
    """Hourly Cost Explorer granularity is not enabled on the payer account."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.setup_url = HOURLY_OPT_IN_SETUP_URL

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
class HourlyCost:
    hour: int
    total: float
    status: str  # "complete" | "partial" | "future"


@dataclass
class TodayUtcDetail:
    date: str
    hourly: list[HourlyCost] = field(default_factory=list)
    spend_so_far: float = 0.0
    projected_total: float | None = None
    projection_available: bool = False


@dataclass
class CostSummary:
    months: list[MonthlyCost] = field(default_factory=list)
    daily_current_month: list[DailyCost] = field(default_factory=list)
    today_utc: TodayUtcDetail | None = None


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


def _ce_client(region: str | None, profile: str | None):
    session = boto3.Session(region_name=region, profile_name=profile)
    return session.client("ce", region_name="us-east-1")


def _monthly_start_date(months: int) -> date:
    today = date.today()
    first_of_current = today.replace(day=1)
    start_month = first_of_current - timedelta(days=1)
    for _ in range(months - 1):
        start_month = start_month.replace(day=1) - timedelta(days=1)
    return start_month.replace(day=1)


def get_cost_months(
    months: int = 6,
    region: str | None = None,
    profile: str | None = None,
) -> list[MonthlyCost]:
    """Fetch monthly totals with per-service breakdowns."""
    ce = _ce_client(region, profile)
    today = date.today()
    start_date = _monthly_start_date(months)
    end_date = today + timedelta(days=1)

    log.debug(
        "get_cost_months range %s to %s", start_date.isoformat(), end_date.isoformat()
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
        period = e["period"][:7]
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

    return sorted(months_map.values(), key=lambda m: m.month)


def get_cost_daily(
    region: str | None = None,
    profile: str | None = None,
) -> list[DailyCost]:
    """Fetch daily totals for the current calendar month."""
    ce = _ce_client(region, profile)
    today = date.today()
    first_of_current = today.replace(day=1)
    daily_start = first_of_current.isoformat()
    daily_end = (today + timedelta(days=1)).isoformat()

    log.debug("get_cost_daily range %s to %s", daily_start, daily_end)

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
    return daily_list


def get_cost_today_utc(
    region: str | None = None,
    profile: str | None = None,
) -> TodayUtcDetail:
    """Fetch today's UTC hourly breakdown and end-of-day projection."""
    ce = _ce_client(region, profile)
    today_utc = datetime.now(timezone.utc).date()
    hourly_start = f"{today_utc.isoformat()}T00:00:00Z"
    hourly_end = f"{(today_utc + timedelta(days=1)).isoformat()}T00:00:00Z"

    log.debug("get_cost_today_utc range %s to %s", hourly_start, hourly_end)

    try:
        hourly_response = ce.get_cost_and_usage(
            TimePeriod={"Start": hourly_start, "End": hourly_end},
            Granularity="HOURLY",
            Metrics=["UnblendedCost"],
        )
    except ClientError as e:
        err = e.response.get("Error", {})
        if err.get("Code") == "AccessDeniedException":
            msg = err.get("Message", str(e))
            if "Hourly" in msg or "opt-in" in msg.lower():
                raise HourlyNotEnabledError(msg) from e
        raise

    return _build_today_utc_detail(
        hourly_response.get("ResultsByTime", []),
        today_utc,
    )


def _parse_hour_from_period(period_start: str) -> int:
    """Extract UTC hour (0-23) from a Cost Explorer TimePeriod Start value."""
    if "T" in period_start:
        dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
        return dt.hour
    # Daily-style date string — treat as hour 0
    return 0


def _build_today_utc_detail(hourly_results: list[dict], today_utc: date) -> TodayUtcDetail:
    """Build today's UTC hourly breakdown and end-of-day projection."""
    now_utc = datetime.now(timezone.utc)
    current_hour = now_utc.hour if now_utc.date() == today_utc else 24

    hour_amounts: dict[int, float] = {}
    for result in hourly_results:
        period_start = result["TimePeriod"]["Start"]
        hour = _parse_hour_from_period(period_start)
        amt = float(result.get("Total", {}).get("UnblendedCost", {}).get("Amount", "0"))
        hour_amounts[hour] = round(amt, 2)

    hourly: list[HourlyCost] = []
    for h in range(24):
        if h > current_hour:
            hourly.append(HourlyCost(hour=h, total=0.0, status="future"))
        elif h == current_hour and h in hour_amounts:
            hourly.append(HourlyCost(hour=h, total=hour_amounts[h], status="partial"))
        elif h <= current_hour:
            hourly.append(HourlyCost(hour=h, total=hour_amounts.get(h, 0.0), status="complete"))
        else:
            hourly.append(HourlyCost(hour=h, total=0.0, status="future"))

    spend_so_far = round(sum(hour_amounts.values()), 2)

    last_complete_hour = current_hour - 1 if current_hour > 0 else None
    if last_complete_hour is not None:
        rate = hour_amounts.get(last_complete_hour, 0.0)
        projected_total = round(rate * 24, 2)
        projection_available = True
    else:
        projected_total = None
        projection_available = False

    return TodayUtcDetail(
        date=today_utc.isoformat(),
        hourly=hourly,
        spend_so_far=spend_so_far,
        projected_total=projected_total,
        projection_available=projection_available,
    )


def get_cost_summary(
    months: int = 6,
    region: str | None = None,
    profile: str | None = None,
) -> CostSummary:
    """Fetch all cost sections. Hourly failures degrade to ``today_utc=None``."""
    monthly_list = get_cost_months(months=months, region=region, profile=profile)
    daily_list = get_cost_daily(region=region, profile=profile)
    try:
        today_detail = get_cost_today_utc(region=region, profile=profile)
    except HourlyNotEnabledError:
        today_detail = None

    return CostSummary(
        months=monthly_list,
        daily_current_month=daily_list,
        today_utc=today_detail,
    )
