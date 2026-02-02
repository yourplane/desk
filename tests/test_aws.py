"""AWS module tests (mocked)."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from desk.aws import (
    DeskVpcOutputs,
    Workstation,
    create_key_pair,
    delete_key_pair,
    get_desk_vpc_outputs,
    get_instance_state,
    get_running_workstations_using_key,
    list_ec2_key_pairs,
    get_latest_ubuntu_ami,
    list_workstations,
    resolve_workstation,
    run_instance,
    start_instance,
    stop_instance,
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
        key_name=None,
    )

    assert result == "i-abc123"
    mock_ec2.run_instances.assert_called_once()
    call_kw = mock_ec2.run_instances.call_args[1]
    assert call_kw["ImageId"] == "ami-123"
    assert call_kw["InstanceType"] == "t3.medium"
    assert "workstation" in str(call_kw["TagSpecifications"])


def test_resolve_workstation_by_id() -> None:
    """resolve_workstation finds by instance ID."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = [
            Workstation(instance_id="i-abc123", name="max", state="running"),
        ]
        assert resolve_workstation("i-abc123") == "i-abc123"


def test_resolve_workstation_by_name() -> None:
    """resolve_workstation finds by name."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = [
            Workstation(instance_id="i-abc123", name="max", state="running"),
        ]
        assert resolve_workstation("max") == "i-abc123"


def test_resolve_workstation_not_found() -> None:
    """resolve_workstation raises when not found."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = []
        with pytest.raises(ValueError, match="not found"):
            resolve_workstation("unknown")


def test_resolve_workstation_multiple_running_same_name() -> None:
    """resolve_workstation errors when multiple running instances share the name."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = [
            Workstation(instance_id="i-aaa", name="main", state="running"),
            Workstation(instance_id="i-bbb", name="main", state="running"),
        ]
        with pytest.raises(ValueError, match="Multiple workstations named 'main'.*i-aaa, i-bbb"):
            resolve_workstation("main")


def test_resolve_workstation_by_name_only_stopped() -> None:
    """resolve_workstation by name finds only running; not found if only stopped."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = [
            Workstation(instance_id="i-abc123", name="main", state="stopped"),
        ]
        with pytest.raises(ValueError, match="not found"):
            resolve_workstation("main")


def test_workstation_dataclass() -> None:
    """Workstation holds instance info."""
    w = Workstation(instance_id="i-123", name="my-box", state="running")
    assert w.instance_id == "i-123"
    assert w.name == "my-box"
    assert w.state == "running"


@patch("desk.aws.boto3.Session")
def test_list_workstations_empty(mock_session: MagicMock) -> None:
    """list_workstations returns empty list when no instances."""
    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{"Reservations": []}]
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_session.return_value.client.return_value = mock_ec2

    result = list_workstations()

    assert result == []


@patch("desk.aws.boto3.Session")
def test_list_workstations_success(mock_session: MagicMock) -> None:
    """list_workstations returns workstations from describe_instances."""
    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-abc123",
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": "Name", "Value": "max"},
                                {"Key": "Type", "Value": "workstation"},
                            ],
                        },
                    ],
                },
            ],
        },
    ]
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_session.return_value.client.return_value = mock_ec2

    result = list_workstations()

    assert len(result) == 1
    assert result[0].instance_id == "i-abc123"
    assert result[0].name == "max"
    assert result[0].state == "running"


@patch("desk.aws.boto3.Session")
def test_list_workstations_missing_name_tag(mock_session: MagicMock) -> None:
    """list_workstations handles instances without Name tag."""
    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-xyz789",
                            "State": {"Name": "stopped"},
                            "Tags": [{"Key": "Type", "Value": "workstation"}],
                        },
                    ],
                },
            ],
        },
    ]
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_session.return_value.client.return_value = mock_ec2

    result = list_workstations()

    assert len(result) == 1
    assert result[0].name == ""


@patch("desk.aws.boto3.Session")
@patch("desk.aws.boto3.Session")
def test_is_ssm_ready_online(mock_session: MagicMock) -> None:
    """is_ssm_ready returns True when PingStatus is Online."""
    mock_ssm = MagicMock()
    mock_ssm.describe_instance_information.return_value = {
        "InstanceInformationList": [
            {"InstanceId": "i-abc123", "PingStatus": "Online"},
        ],
    }
    mock_session.return_value.client.return_value = mock_ssm

    assert is_ssm_ready("i-abc123") is True


@patch("desk.aws.boto3.Session")
def test_is_ssm_ready_not_registered(mock_session: MagicMock) -> None:
    """is_ssm_ready returns False when instance not in SSM."""
    mock_ssm = MagicMock()
    mock_ssm.describe_instance_information.return_value = {"InstanceInformationList": []}
    mock_session.return_value.client.return_value = mock_ssm

    assert is_ssm_ready("i-xyz789") is False


@patch("desk.aws.boto3.Session")
def test_list_ec2_key_pairs(mock_session: MagicMock) -> None:
    """list_ec2_key_pairs returns key names from AWS."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_key_pairs.return_value = {
        "KeyPairs": [
            {"KeyName": "my-key"},
            {"KeyName": "other-key"},
        ],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = list_ec2_key_pairs()

    assert result == {"my-key", "other-key"}


@patch("desk.aws.boto3.Session")
def test_get_running_workstations_using_key(mock_session: MagicMock) -> None:
    """get_running_workstations_using_key returns instance IDs."""
    mock_ec2 = MagicMock()
    mock_ec2.get_paginator.return_value.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {"InstanceId": "i-abc123"},
                    ],
                },
            ],
        },
    ]
    mock_session.return_value.client.return_value = mock_ec2

    result = get_running_workstations_using_key("my-key")

    assert result == ["i-abc123"]


@patch("desk.aws.boto3.Session")
def test_delete_key_pair(mock_session: MagicMock) -> None:
    """delete_key_pair calls AWS API."""
    mock_ec2 = MagicMock()
    mock_session.return_value.client.return_value = mock_ec2

    delete_key_pair("my-key")

    mock_ec2.delete_key_pair.assert_called_once_with(KeyName="my-key")


@patch("desk.aws.boto3.Session")
def test_create_key_pair_success(mock_session: MagicMock) -> None:
    """create_key_pair returns key material from AWS."""
    mock_ec2 = MagicMock()
    mock_ec2.create_key_pair.return_value = {
        "KeyMaterial": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----",
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = create_key_pair("my-key")

    assert "BEGIN RSA PRIVATE KEY" in result
    mock_ec2.create_key_pair.assert_called_once_with(KeyName="my-key")


@patch("desk.aws.boto3.Session")
def test_stop_instance_success(mock_session: MagicMock) -> None:
    """stop_instance calls stop_instances and returns instance ID."""
    mock_ec2 = MagicMock()
    mock_session.return_value.client.return_value = mock_ec2

    result = stop_instance("i-abc123")

    assert result == "i-abc123"
    mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@patch("desk.aws.boto3.Session")
def test_start_instance_success(mock_session: MagicMock) -> None:
    """start_instance calls start_instances and returns instance ID."""
    mock_ec2 = MagicMock()
    mock_session.return_value.client.return_value = mock_ec2

    result = start_instance("i-abc123")

    assert result == "i-abc123"
    mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-abc123"])


def test_resolve_workstation_by_name_with_stopped_states() -> None:
    """resolve_workstation with states=['stopped'] finds stopped instances."""
    with patch("desk.aws.list_workstations") as mock_list:
        mock_list.return_value = [
            Workstation(instance_id="i-abc123", name="main", state="stopped"),
        ]
        result = resolve_workstation("main", states=["stopped"])
        assert result == "i-abc123"


@patch("desk.aws.boto3.Session")
def test_get_instance_state_success(mock_session: MagicMock) -> None:
    """get_instance_state returns the instance state."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {"InstanceId": "i-abc123", "State": {"Name": "stopped"}},
                ],
            },
        ],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = get_instance_state("i-abc123")

    assert result == "stopped"
    mock_ec2.describe_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@patch("desk.aws.boto3.Session")
def test_get_instance_state_not_found(mock_session: MagicMock) -> None:
    """get_instance_state returns None when instance not found."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {"Reservations": []}
    mock_session.return_value.client.return_value = mock_ec2

    result = get_instance_state("i-nonexistent")

    assert result is None
