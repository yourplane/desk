"""Cost Explorer module tests (mocked)."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from desk.costs import (
    CostSummary,
    DailyCost,
    MonthlyCost,
    ServiceCost,
    TodayUtcDetail,
    _build_today_utc_detail,
    _friendly_name,
    _category,
    get_cost_summary,
)


def test_friendly_name_known() -> None:
    assert _friendly_name("Amazon Elastic Compute Cloud - Compute") == "EC2 Instances"
    assert _friendly_name("EC2 - Other") == "EC2 Other (NAT, EBS, EIPs)"
    assert _friendly_name("Amazon Simple Storage Service") == "S3"


def test_friendly_name_unknown() -> None:
    assert _friendly_name("SomeNewService") == "SomeNewService"


def test_category_ec2_instances() -> None:
    assert _category("EC2 Instances") == "EC2 Instances"


def test_category_ec2_infra() -> None:
    assert _category("EC2 Other (NAT, EBS, EIPs)") == "EC2 Infrastructure"
    assert _category("VPC") == "EC2 Infrastructure"


def test_category_other() -> None:
    assert _category("S3") == "Other"
    assert _category("Lambda") == "Other"


@patch("desk.costs.boto3.Session")
def test_get_cost_summary_success(mock_session: MagicMock) -> None:
    """get_cost_summary returns monthly and daily cost data."""
    mock_ce = MagicMock()
    mock_session.return_value.client.return_value = mock_ce

    mock_ce.get_cost_and_usage.side_effect = [
        # Monthly response
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-02-01", "End": "2026-03-01"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                            "Metrics": {"UnblendedCost": {"Amount": "85.20", "Unit": "USD"}},
                        },
                        {
                            "Keys": ["EC2 - Other"],
                            "Metrics": {"UnblendedCost": {"Amount": "33.40", "Unit": "USD"}},
                        },
                    ],
                },
                {
                    "TimePeriod": {"Start": "2026-03-01", "End": "2026-04-01"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud - Compute"],
                            "Metrics": {"UnblendedCost": {"Amount": "42.10", "Unit": "USD"}},
                        },
                        {
                            "Keys": ["Amazon Simple Storage Service"],
                            "Metrics": {"UnblendedCost": {"Amount": "1.25", "Unit": "USD"}},
                        },
                    ],
                },
            ],
        },
        # Daily response
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-01", "End": "2026-03-02"},
                    "Total": {"UnblendedCost": {"Amount": "4.50", "Unit": "USD"}},
                },
                {
                    "TimePeriod": {"Start": "2026-03-02", "End": "2026-03-03"},
                    "Total": {"UnblendedCost": {"Amount": "5.10", "Unit": "USD"}},
                },
            ],
        },
        # Hourly response (today UTC)
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-02T08:00:00Z", "End": "2026-03-02T09:00:00Z"},
                    "Total": {"UnblendedCost": {"Amount": "0.20", "Unit": "USD"}},
                },
                {
                    "TimePeriod": {"Start": "2026-03-02T09:00:00Z", "End": "2026-03-02T10:00:00Z"},
                    "Total": {"UnblendedCost": {"Amount": "0.25", "Unit": "USD"}},
                },
            ],
        },
    ]

    result = get_cost_summary(months=2)

    assert isinstance(result, CostSummary)
    assert len(result.months) == 2

    feb = result.months[0]
    assert feb.month == "2026-02"
    assert feb.total == 118.6
    assert len(feb.services) == 2
    assert feb.services[0].service == "EC2 Instances"
    assert feb.services[0].amount == 85.20
    assert feb.services[0].category == "EC2 Instances"
    assert feb.services[1].service == "EC2 Other (NAT, EBS, EIPs)"
    assert feb.services[1].category == "EC2 Infrastructure"

    mar = result.months[1]
    assert mar.month == "2026-03"
    assert mar.total == 43.35

    assert len(result.daily_current_month) == 2
    assert result.daily_current_month[0].date == "2026-03-01"
    assert result.daily_current_month[0].total == 4.50
    assert result.daily_current_month[1].total == 5.10

    assert result.today_utc is not None
    assert len(result.today_utc.hourly) == 24


@patch("desk.costs.boto3.Session")
def test_get_cost_summary_filters_tiny_amounts(mock_session: MagicMock) -> None:
    """Services with near-zero amounts are excluded."""
    mock_ce = MagicMock()
    mock_session.return_value.client.return_value = mock_ce

    mock_ce.get_cost_and_usage.side_effect = [
        {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2026-03-01", "End": "2026-04-01"},
                    "Groups": [
                        {
                            "Keys": ["AWS Lambda"],
                            "Metrics": {"UnblendedCost": {"Amount": "0.001", "Unit": "USD"}},
                        },
                        {
                            "Keys": ["Amazon Simple Storage Service"],
                            "Metrics": {"UnblendedCost": {"Amount": "2.50", "Unit": "USD"}},
                        },
                    ],
                },
            ],
        },
        {"ResultsByTime": []},
        {"ResultsByTime": []},
    ]

    result = get_cost_summary(months=1)

    assert len(result.months) == 1
    mar = result.months[0]
    assert len(mar.services) == 1
    assert mar.services[0].service == "S3"


@patch("desk.costs.boto3.Session")
def test_get_cost_summary_empty(mock_session: MagicMock) -> None:
    """get_cost_summary returns empty when no data."""
    mock_ce = MagicMock()
    mock_session.return_value.client.return_value = mock_ce

    mock_ce.get_cost_and_usage.side_effect = [
        {"ResultsByTime": []},
        {"ResultsByTime": []},
        {"ResultsByTime": []},
    ]

    result = get_cost_summary(months=6)

    assert isinstance(result, CostSummary)
    assert result.months == []
    assert result.daily_current_month == []
    assert result.today_utc is not None


@patch("desk.costs.datetime")
def test_build_today_utc_detail_projection(mock_dt: MagicMock) -> None:
    """Projection uses last complete hour rate × 24."""
    mock_dt.now.return_value = datetime(2026, 3, 2, 10, 30, tzinfo=timezone.utc)
    mock_dt.fromisoformat = datetime.fromisoformat

    today = date(2026, 3, 2)
    hourly_results = [
        {
            "TimePeriod": {"Start": "2026-03-02T08:00:00Z", "End": "2026-03-02T09:00:00Z"},
            "Total": {"UnblendedCost": {"Amount": "0.20", "Unit": "USD"}},
        },
        {
            "TimePeriod": {"Start": "2026-03-02T09:00:00Z", "End": "2026-03-02T10:00:00Z"},
            "Total": {"UnblendedCost": {"Amount": "0.30", "Unit": "USD"}},
        },
        {
            "TimePeriod": {"Start": "2026-03-02T10:00:00Z", "End": "2026-03-02T11:00:00Z"},
            "Total": {"UnblendedCost": {"Amount": "0.15", "Unit": "USD"}},
        },
    ]

    detail = _build_today_utc_detail(hourly_results, today)

    assert isinstance(detail, TodayUtcDetail)
    assert detail.spend_so_far == 0.65
    assert detail.projection_available is True
    assert detail.projected_total == 7.20  # 0.30 × 24 (hour 9 is last complete)
    assert detail.hourly[9].status == "complete"
    assert detail.hourly[10].status == "partial"
    assert detail.hourly[11].status == "future"


@patch("desk.costs.datetime")
def test_build_today_utc_detail_no_projection_early_day(mock_dt: MagicMock) -> None:
    """Before the first complete hour, projection is unavailable."""
    mock_dt.now.return_value = datetime(2026, 3, 2, 0, 30, tzinfo=timezone.utc)
    mock_dt.fromisoformat = datetime.fromisoformat

    today = date(2026, 3, 2)
    hourly_results = [
        {
            "TimePeriod": {"Start": "2026-03-02T00:00:00Z", "End": "2026-03-02T01:00:00Z"},
            "Total": {"UnblendedCost": {"Amount": "0.05", "Unit": "USD"}},
        },
    ]

    detail = _build_today_utc_detail(hourly_results, today)

    assert detail.projection_available is False
    assert detail.projected_total is None
    assert detail.hourly[0].status == "partial"


@patch("desk.costs.datetime")
@patch("desk.costs.boto3.Session")
def test_get_cost_summary_hourly_time_period_format(
    mock_session: MagicMock, mock_dt: MagicMock
) -> None:
    """Hourly Cost Explorer calls require yyyy-MM-ddThh:mm:ssZ TimePeriod bounds."""
    mock_dt.now.return_value = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    mock_dt.fromisoformat = datetime.fromisoformat

    mock_ce = MagicMock()
    mock_session.return_value.client.return_value = mock_ce
    mock_ce.get_cost_and_usage.side_effect = [
        {"ResultsByTime": []},
        {"ResultsByTime": []},
        {"ResultsByTime": []},
    ]

    get_cost_summary(months=1)

    hourly_call = mock_ce.get_cost_and_usage.call_args_list[2]
    assert hourly_call.kwargs["Granularity"] == "HOURLY"
    assert hourly_call.kwargs["TimePeriod"] == {
        "Start": "2026-07-27T00:00:00Z",
        "End": "2026-07-28T00:00:00Z",
    }
