"""Tests for router infra idle/wake helpers."""

from unittest.mock import MagicMock, patch

import pytest

from desk.aws import Workstation
from desk.router_infra import (
    DemandSource,
    get_router_infra_demand_sources,
    get_router_infra_status,
    reconcile_router_infra,
    router_infra_demand_exists,
    router_infra_friendly_label,
    sleep_router_infra,
)


@patch("desk.router_infra.get_router_infra_demand_sources")
def test_router_infra_demand_exists_true(mock_sources: MagicMock) -> None:
    mock_sources.return_value = [
        DemandSource(name="main", ports=[5173], state="running"),
    ]
    assert router_infra_demand_exists() is True


@patch("desk.router_infra.get_router_infra_demand_sources")
def test_router_infra_demand_exists_false_stopped(mock_sources: MagicMock) -> None:
    mock_sources.return_value = []
    assert router_infra_demand_exists() is False


@patch("desk.router_infra.get_router_infra_demand_sources")
def test_router_infra_friendly_labels(mock_sources: MagicMock) -> None:
    mock_sources.return_value = []
    assert router_infra_friendly_label("idle") == "Stopped"
    assert router_infra_friendly_label("active") == "Running"
    assert router_infra_friendly_label("waking") == "Starting"
    assert router_infra_friendly_label("sleeping") == "Stopping"


@patch("desk.router_infra._session")
def test_get_router_infra_status_idle(mock_session: MagicMock) -> None:
    from botocore.exceptions import ClientError as BotoClientError

    cf = MagicMock()

    def describe_side_effect(StackName=None, **kwargs):
        if StackName == "desk-router":
            return {
                "Stacks": [{
                    "StackStatus": "UPDATE_COMPLETE",
                    "Outputs": [{"OutputKey": "RouterAsgName", "OutputValue": "desk-router-asg"}],
                    "Parameters": [],
                }]
            }
        raise BotoClientError({"Error": {"Code": "ValidationError", "Message": "not found"}}, "DescribeStacks")

    cf.describe_stacks.side_effect = describe_side_effect
    asg = MagicMock()
    asg.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [{"DesiredCapacity": 0, "Instances": []}],
    }
    mock_session.return_value.client.side_effect = lambda svc, **kw: cf if svc == "cloudformation" else asg
    mock_session.return_value.region_name = "us-east-1"
    mock_session.return_value.profile_name = None

    with patch("desk.router_infra.get_router_infra_demand_sources", return_value=[]):
        status = get_router_infra_status()
    assert status.phase == "idle"
    assert status.active_stack_present is False
    assert status.demand_sources == []


@patch("desk.web_routes.prune_stale_web_routes")
@patch("desk.router_infra.list_all_web_routes")
@patch("desk.router_infra.list_workstations")
def test_get_router_infra_demand_sources(
    mock_list_ws: MagicMock,
    mock_routes: MagicMock,
    mock_prune: MagicMock,
) -> None:
    mock_routes.return_value = {"main": [5173], "gone": [8080]}
    mock_list_ws.return_value = [
        Workstation(instance_id="i-1", name="main", state="pending", shutdown_at=None, image_id="ami-1"),
    ]
    sources = get_router_infra_demand_sources(prune_stale=False)
    assert len(sources) == 1
    assert sources[0].name == "main"
    assert sources[0].ports == [5173]
    mock_prune.assert_not_called()


@patch("desk.router_infra.wake_router_infra")
@patch("desk.router_infra.get_router_infra_status")
@patch("desk.router_infra.router_infra_demand_exists")
def test_reconcile_wake_on_demand(
    mock_demand: MagicMock,
    mock_status: MagicMock,
    mock_wake: MagicMock,
) -> None:
    from desk.router_infra import RouterInfraStatus

    mock_demand.return_value = True
    mock_status.return_value = RouterInfraStatus(phase="idle", active_stack_present=False)
    mock_wake.return_value = {"step": "create_active_stack"}
    result = reconcile_router_infra()
    assert result["action"] == "wake"
    mock_wake.assert_called_once()


@patch("desk.router_infra.sleep_router_infra")
@patch("desk.router_infra.get_router_infra_status")
@patch("desk.router_infra.router_infra_demand_exists")
def test_reconcile_sleep_when_idle(
    mock_demand: MagicMock,
    mock_status: MagicMock,
    mock_sleep: MagicMock,
) -> None:
    from desk.router_infra import RouterInfraStatus

    mock_demand.return_value = False
    mock_status.return_value = RouterInfraStatus(
        phase="active",
        active_stack_present=True,
        asg_desired=1,
        base_stack_status="UPDATE_COMPLETE",
        active_stack_status="CREATE_COMPLETE",
    )
    mock_sleep.return_value = {"step": "sleep"}
    result = reconcile_router_infra()
    assert result["action"] == "sleep"


@patch("desk.router_infra._update_base_active_params")
@patch("desk.router_infra._stack_status")
@patch("desk.router_infra._session")
def test_sleep_router_infra_rejects_demand(
    mock_session: MagicMock,
    mock_stack_status: MagicMock,
    mock_update: MagicMock,
) -> None:
    mock_stack_status.return_value = "UPDATE_COMPLETE"
    with patch("desk.router_infra.router_infra_demand_exists", return_value=True):
        with pytest.raises(ValueError, match="still needed"):
            sleep_router_infra()
