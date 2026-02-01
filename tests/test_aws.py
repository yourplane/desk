"""AWS module tests (mocked)."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from desk.aws import (
    DeskVpcOutputs,
    get_desk_vpc_outputs,
    get_latest_ubuntu_ami,
    run_instance,
)


def test_desk_vpc_outputs_dataclass() -> None:
    """DeskVpcOutputs holds stack outputs."""
    outputs = DeskVpcOutputs(
        vpc_id="vpc-123",
        private_subnet_ids=["subnet-a", "subnet-b"],
        security_group_id="sg-456",
        instance_profile_name="desk-workstation-profile",
    )
    assert outputs.vpc_id == "vpc-123"
    assert outputs.private_subnet_ids == ["subnet-a", "subnet-b"]


@patch("desk.aws.boto3.Session")
def test_get_desk_vpc_outputs_success(mock_session: MagicMock) -> None:
    """get_desk_vpc_outputs returns outputs from stack."""
    mock_cf = MagicMock()
    mock_cf.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "VpcId", "OutputValue": "vpc-123"},
                    {"OutputKey": "PrivateSubnetIds", "OutputValue": "subnet-a, subnet-b"},
                    {"OutputKey": "WorkstationSecurityGroupId", "OutputValue": "sg-456"},
                    {"OutputKey": "WorkstationInstanceProfile", "OutputValue": "desk-profile"},
                ],
            },
        ],
    }
    mock_session.return_value.client.return_value = mock_cf

    result = get_desk_vpc_outputs(stack_name="desk")

    assert result.vpc_id == "vpc-123"
    assert result.private_subnet_ids == ["subnet-a", "subnet-b"]
    assert result.security_group_id == "sg-456"
    assert result.instance_profile_name == "desk-profile"


@patch("desk.aws.boto3.Session")
def test_get_desk_vpc_outputs_stack_not_found(mock_session: MagicMock) -> None:
    """get_desk_vpc_outputs raises when stack does not exist."""
    mock_cf = MagicMock()
    mock_cf.describe_stacks.side_effect = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
        "DescribeStacks",
    )
    mock_session.return_value.client.return_value = mock_cf

    with pytest.raises(RuntimeError, match="Stack 'desk' not found"):
        get_desk_vpc_outputs(stack_name="desk")


@patch("desk.aws.boto3.Session")
def test_get_latest_ubuntu_ami_success(mock_session: MagicMock) -> None:
    """get_latest_ubuntu_ami returns newest AMI ID."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-old", "CreationDate": "2024-01-01"},
            {"ImageId": "ami-new", "CreationDate": "2024-06-01"},
        ],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = get_latest_ubuntu_ami()

    assert result == "ami-new"
    mock_ec2.describe_images.assert_called_once()


@patch("desk.aws.boto3.Session")
def test_get_latest_ubuntu_ami_none_found(mock_session: MagicMock) -> None:
    """get_latest_ubuntu_ami raises when no AMI matches."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {"Images": []}
    mock_session.return_value.client.return_value = mock_ec2

    with pytest.raises(RuntimeError, match="No Ubuntu .* AMI found"):
        get_latest_ubuntu_ami()


@patch("desk.aws.boto3.Session")
def test_run_instance_success(mock_session: MagicMock) -> None:
    """run_instance returns instance ID."""
    mock_ec2 = MagicMock()
    mock_ec2.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-abc123"}],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = run_instance(
        ami_id="ami-123",
        instance_type="t3.medium",
        subnet_id="subnet-a",
        security_group_ids=["sg-456"],
        iam_instance_profile_name="desk-profile",
        name="my-workstation",
    )

    assert result == "i-abc123"
    mock_ec2.run_instances.assert_called_once()
    call_kw = mock_ec2.run_instances.call_args[1]
    assert call_kw["ImageId"] == "ami-123"
    assert call_kw["InstanceType"] == "t3.medium"
    assert "workstation" in str(call_kw["TagSpecifications"])
