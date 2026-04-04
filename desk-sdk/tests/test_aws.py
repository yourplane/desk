"""AWS module tests (mocked)."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from desk.aws import (
    AmiInfo,
    DeskVpcOutputs,
    Workstation,
    create_ami,
    create_key_pair,
    create_workstation,
    delete_key_pair,
    generate_presigned_get_object_url,
    get_ami_state,
    get_desk_copy_bucket,
    get_desk_vpc_outputs,
    get_instance_state,
    get_latest_ami_by_name_prefix,
    get_latest_ubuntu_ami,
    get_running_workstations_using_key,
    get_ssm_command,
    is_ssm_ready,
    list_amis,
    list_ec2_key_pairs,
    list_s3_object_keys_under_prefix,
    list_workstations,
    resolve_workstation,
    run_workstation,
    start_workstation,
    stop_instance,
    terminate_instance,
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
def test_get_desk_copy_bucket_success(mock_session: MagicMock) -> None:
    """get_desk_copy_bucket returns bucket name from stack output."""
    mock_cf = MagicMock()
    mock_cf.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "DeskCopyBucketName", "OutputValue": "desk-123-us-east-1-copy"},
                ],
            },
        ],
    }
    mock_session.return_value.client.return_value = mock_cf
    mock_session.return_value.region_name = "us-east-1"

    result = get_desk_copy_bucket(stack_name="desk")
    assert result == "desk-123-us-east-1-copy"


@patch("desk.aws.boto3.Session")
def test_get_desk_copy_bucket_stack_not_found(mock_session: MagicMock) -> None:
    """get_desk_copy_bucket raises when stack does not exist."""
    mock_cf = MagicMock()
    mock_cf.describe_stacks.side_effect = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}},
        "DescribeStacks",
    )
    mock_session.return_value.client.return_value = mock_cf

    with pytest.raises(RuntimeError, match="Stack 'desk' not found"):
        get_desk_copy_bucket(stack_name="desk")


@patch("desk.aws.boto3.Session")
def test_get_desk_copy_bucket_missing_output(mock_session: MagicMock) -> None:
    """get_desk_copy_bucket raises when DeskCopyBucketName output is missing."""
    mock_cf = MagicMock()
    mock_cf.describe_stacks.return_value = {
        "Stacks": [{"Outputs": [{"OutputKey": "VpcId", "OutputValue": "vpc-123"}]}],
    }
    mock_session.return_value.client.return_value = mock_cf

    with pytest.raises(RuntimeError, match="no DeskCopyBucketName output"):
        get_desk_copy_bucket(stack_name="desk")


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
def test_get_latest_ami_by_name_prefix_success(mock_session: MagicMock) -> None:
    """get_latest_ami_by_name_prefix returns newest AMI whose name starts with prefix."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-old", "Name": "default-desk-ami-20240101-120000", "CreationDate": "2024-01-01"},
            {"ImageId": "ami-new", "Name": "default-desk-ami-20240601-120000", "CreationDate": "2024-06-01"},
            {"ImageId": "ami-other", "Name": "other-ami", "CreationDate": "2024-05-01"},
        ],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = get_latest_ami_by_name_prefix("default-desk-ami")

    assert result == "ami-new"
    mock_ec2.describe_images.assert_called_once_with(
        Owners=["self"], Filters=[{"Name": "state", "Values": ["available"]}]
    )


@patch("desk.aws.boto3.Session")
def test_get_latest_ami_by_name_prefix_none_found(mock_session: MagicMock) -> None:
    """get_latest_ami_by_name_prefix returns None when no AMI name matches prefix."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-other", "Name": "other-ami", "CreationDate": "2024-01-01"},
        ],
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = get_latest_ami_by_name_prefix("default-desk-ami")

    assert result is None


@patch("desk.aws.boto3.Session")
def test_run_workstation_success(mock_session: MagicMock) -> None:
    """run_workstation launches instance and sets shutdown tag; returns (instance_id, shutdown_at)."""
    mock_ec2 = MagicMock()
    mock_ec2.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-abc123"}],
    }
    mock_session.return_value.client.return_value = mock_ec2

    instance_id, shutdown_at = run_workstation(
        ami_id="ami-123",
        instance_type="t3.medium",
        subnet_id="subnet-a",
        security_group_ids=["sg-456"],
        iam_instance_profile_name="desk-profile",
        name="my-workstation",
        shutdown_after="4h",
        key_name=None,
    )

    assert instance_id == "i-abc123"
    assert shutdown_at is not None
    assert "T" in shutdown_at and "Z" in shutdown_at
    mock_ec2.run_instances.assert_called_once()
    call_kw = mock_ec2.run_instances.call_args[1]
    assert call_kw["ImageId"] == "ami-123"
    assert call_kw["InstanceType"] == "t3.medium"
    assert "workstation" in str(call_kw["TagSpecifications"])
    bdm = call_kw["BlockDeviceMappings"]
    assert len(bdm) == 1
    assert bdm[0]["DeviceName"] == "/dev/sda1"
    assert bdm[0]["Ebs"]["VolumeSize"] == 32
    assert bdm[0]["Ebs"]["VolumeType"] == "gp3"
    assert bdm[0]["Ebs"]["DeleteOnTermination"] is True
    mock_ec2.create_tags.assert_called_once()


@patch("desk.aws.run_workstation")
@patch("desk.aws.get_latest_ubuntu_ami")
@patch("desk.aws.get_desk_vpc_outputs")
@patch("desk.config.get_desk_settings")
@patch("desk.aws.list_workstations")
def test_create_workstation_success(
    mock_list: MagicMock,
    mock_settings: MagicMock,
    mock_vpc: MagicMock,
    mock_ubuntu_ami: MagicMock,
    mock_run: MagicMock,
) -> None:
    """create_workstation validates name, resolves VPC/AMI, and launches."""
    mock_list.return_value = []
    mock_settings.return_value = MagicMock(ami_prefix=None)
    mock_vpc.return_value = DeskVpcOutputs(
        vpc_id="vpc-1",
        private_subnet_ids=["subnet-a"],
        security_group_id="sg-1",
        instance_profile_name="profile-1",
    )
    mock_ubuntu_ami.return_value = "ami-ubuntu"
    mock_run.return_value = ("i-new123", "2026-03-20T20:00:00Z")

    instance_id, shutdown_at = create_workstation("my-ws")

    assert instance_id == "i-new123"
    assert shutdown_at == "2026-03-20T20:00:00Z"
    mock_run.assert_called_once()
    kw = mock_run.call_args[1]
    assert kw["ami_id"] == "ami-ubuntu"
    assert kw["instance_type"] == "t3.medium"
    assert kw["subnet_id"] == "subnet-a"
    assert kw["name"] == "my-ws"
    assert kw["shutdown_after"] == "4h"


@patch("desk.aws.run_workstation")
@patch("desk.aws.get_latest_ami_by_name_prefix")
@patch("desk.aws.get_desk_vpc_outputs")
@patch("desk.config.get_desk_settings")
@patch("desk.aws.list_workstations")
def test_create_workstation_uses_ami_prefix(
    mock_list: MagicMock,
    mock_settings: MagicMock,
    mock_vpc: MagicMock,
    mock_ami_by_prefix: MagicMock,
    mock_run: MagicMock,
) -> None:
    """create_workstation resolves AMI via configured prefix when available."""
    mock_list.return_value = []
    mock_settings.return_value = MagicMock(ami_prefix="default-desk-ami")
    mock_ami_by_prefix.return_value = "ami-custom"
    mock_vpc.return_value = DeskVpcOutputs(
        vpc_id="vpc-1",
        private_subnet_ids=["subnet-a"],
        security_group_id="sg-1",
        instance_profile_name="profile-1",
    )
    mock_run.return_value = ("i-new456", "2026-03-20T20:00:00Z")

    instance_id, _ = create_workstation("ws", ami_id=None)

    kw = mock_run.call_args[1]
    assert kw["ami_id"] == "ami-custom"


@patch("desk.aws.run_workstation")
@patch("desk.aws.get_desk_vpc_outputs")
@patch("desk.config.get_desk_settings")
@patch("desk.aws.list_workstations")
def test_create_workstation_explicit_ami(
    mock_list: MagicMock,
    mock_settings: MagicMock,
    mock_vpc: MagicMock,
    mock_run: MagicMock,
) -> None:
    """create_workstation skips AMI resolution when ami_id is provided."""
    mock_list.return_value = []
    mock_settings.return_value = MagicMock(ami_prefix="default-desk-ami")
    mock_vpc.return_value = DeskVpcOutputs(
        vpc_id="vpc-1",
        private_subnet_ids=["subnet-a"],
        security_group_id="sg-1",
        instance_profile_name="profile-1",
    )
    mock_run.return_value = ("i-new789", None)

    create_workstation("ws", ami_id="ami-explicit", shutdown_after="0")

    kw = mock_run.call_args[1]
    assert kw["ami_id"] == "ami-explicit"


@patch("desk.aws.list_workstations")
def test_create_workstation_rejects_duplicate_running(mock_list: MagicMock) -> None:
    """create_workstation raises ValueError for duplicate running workstation."""
    mock_list.return_value = [
        Workstation(instance_id="i-existing", name="my-ws", state="running"),
    ]

    with pytest.raises(ValueError, match="already exists"):
        create_workstation("my-ws")


@patch("desk.aws.list_workstations")
def test_create_workstation_rejects_duplicate_stopped(mock_list: MagicMock) -> None:
    """create_workstation raises ValueError for duplicate stopped workstation."""
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="my-ws", state="stopped"),
    ]

    with pytest.raises(ValueError, match="already exists"):
        create_workstation("my-ws")


@patch("desk.aws.run_workstation")
@patch("desk.aws.get_latest_ubuntu_ami")
@patch("desk.aws.get_desk_vpc_outputs")
@patch("desk.config.get_desk_settings")
@patch("desk.aws.list_workstations")
def test_create_workstation_allows_terminated_duplicate(
    mock_list: MagicMock,
    mock_settings: MagicMock,
    mock_vpc: MagicMock,
    mock_ubuntu_ami: MagicMock,
    mock_run: MagicMock,
) -> None:
    """create_workstation allows name reuse when existing workstation is terminated."""
    mock_list.return_value = [
        Workstation(instance_id="i-old", name="my-ws", state="terminated"),
    ]
    mock_settings.return_value = MagicMock(ami_prefix=None)
    mock_vpc.return_value = DeskVpcOutputs(
        vpc_id="vpc-1",
        private_subnet_ids=["subnet-a"],
        security_group_id="sg-1",
        instance_profile_name="profile-1",
    )
    mock_ubuntu_ami.return_value = "ami-ubuntu"
    mock_run.return_value = ("i-new", "2026-03-20T20:00:00Z")

    instance_id, _ = create_workstation("my-ws")

    assert instance_id == "i-new"
    mock_run.assert_called_once()


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
        # Mock returns empty when filtering for running/pending (default states)
        mock_list.return_value = []
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
def test_start_workstation_success(mock_session: MagicMock) -> None:
    """start_workstation starts instance and sets shutdown tag; returns (instance_id, shutdown_at)."""
    mock_ec2 = MagicMock()
    mock_session.return_value.client.return_value = mock_ec2

    instance_id, shutdown_at = start_workstation("i-abc123", "4h")

    assert instance_id == "i-abc123"
    assert shutdown_at is not None
    assert "T" in shutdown_at and "Z" in shutdown_at
    mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-abc123"])
    mock_ec2.create_tags.assert_called_once()


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


@patch("desk.aws.boto3.Session")
def test_terminate_instance_success(mock_session: MagicMock) -> None:
    """terminate_instance calls terminate_instances and returns instance ID."""
    mock_ec2 = MagicMock()
    mock_session.return_value.client.return_value = mock_ec2

    result = terminate_instance("i-abc123")

    assert result == "i-abc123"
    mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@patch("desk.aws.boto3.Session")
def test_create_ami_success(mock_session: MagicMock) -> None:
    """create_ami returns AMI ID."""
    mock_ec2 = MagicMock()
    mock_ec2.create_image.return_value = {"ImageId": "ami-12345"}
    mock_session.return_value.client.return_value = mock_ec2

    result = create_ami(
        instance_id="i-abc123",
        name="my-ami",
        description="Test AMI",
    )

    assert result == "ami-12345"
    mock_ec2.create_image.assert_called_once()
    call_kwargs = mock_ec2.create_image.call_args[1]
    assert call_kwargs["InstanceId"] == "i-abc123"
    assert call_kwargs["Name"] == "my-ami"
    assert call_kwargs["Description"] == "Test AMI"
    assert "desk:managed" in str(call_kwargs["TagSpecifications"])


@patch("desk.aws.boto3.Session")
def test_create_ami_no_reboot(mock_session: MagicMock) -> None:
    """create_ami passes NoReboot flag."""
    mock_ec2 = MagicMock()
    mock_ec2.create_image.return_value = {"ImageId": "ami-12345"}
    mock_session.return_value.client.return_value = mock_ec2

    result = create_ami(
        instance_id="i-abc123",
        name="my-ami",
        no_reboot=True,
    )

    assert result == "ami-12345"
    call_kwargs = mock_ec2.create_image.call_args[1]
    assert call_kwargs["NoReboot"] is True


@patch("desk.aws.boto3.Session")
def test_get_ami_state_available(mock_session: MagicMock) -> None:
    """get_ami_state returns state when AMI exists."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [{"ImageId": "ami-12345", "State": "available"}]
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = get_ami_state("ami-12345")

    assert result == "available"
    mock_ec2.describe_images.assert_called_once_with(ImageIds=["ami-12345"])


@patch("desk.aws.boto3.Session")
def test_get_ami_state_not_found(mock_session: MagicMock) -> None:
    """get_ami_state returns None when AMI not found."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.side_effect = ClientError(
        {"Error": {"Code": "InvalidAMIID.NotFound", "Message": "not found"}},
        "DescribeImages",
    )
    mock_session.return_value.client.return_value = mock_ec2

    result = get_ami_state("ami-nonexistent")

    assert result is None


@patch("desk.aws.boto3.Session")
def test_get_ami_state_empty_result(mock_session: MagicMock) -> None:
    """get_ami_state returns None when no images returned."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {"Images": []}
    mock_session.return_value.client.return_value = mock_ec2

    result = get_ami_state("ami-12345")

    assert result is None


@patch("desk.aws.boto3.Session")
def test_list_amis_success(mock_session: MagicMock) -> None:
    """list_amis returns desk-managed AMIs sorted by creation date descending."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [
            {
                "ImageId": "ami-old",
                "Name": "old-ami",
                "State": "available",
                "CreationDate": "2025-01-01T10:00:00.000Z",
                "Tags": [
                    {"Key": "desk:managed", "Value": "true"},
                    {"Key": "desk:source-instance", "Value": "i-aaa"},
                ],
            },
            {
                "ImageId": "ami-new",
                "Name": "new-ami",
                "State": "available",
                "CreationDate": "2025-02-01T12:00:00.000Z",
                "Tags": [
                    {"Key": "desk:managed", "Value": "true"},
                    {"Key": "desk:source-instance", "Value": "i-bbb"},
                ],
            },
        ]
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = list_amis()

    assert len(result) == 2
    assert result[0].image_id == "ami-new"
    assert result[0].name == "new-ami"
    assert result[0].state == "available"
    assert result[0].creation_date == "2025-02-01T12:00:00.000Z"
    assert result[0].source_instance == "i-bbb"
    assert result[1].image_id == "ami-old"
    assert result[1].source_instance == "i-aaa"
    mock_ec2.describe_images.assert_called_once_with(
        Owners=["self"],
        Filters=[{"Name": "tag:desk:managed", "Values": ["true"]}],
    )


@patch("desk.aws.boto3.Session")
def test_list_amis_empty(mock_session: MagicMock) -> None:
    """list_amis returns empty list when no desk AMIs exist."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {"Images": []}
    mock_session.return_value.client.return_value = mock_ec2

    result = list_amis()

    assert result == []
    mock_ec2.describe_images.assert_called_once_with(
        Owners=["self"],
        Filters=[{"Name": "tag:desk:managed", "Values": ["true"]}],
    )


@patch("desk.aws.boto3.Session")
def test_list_amis_all_owned(mock_session: MagicMock) -> None:
    """list_amis with managed_only=False does not filter by desk tag."""
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {
        "Images": [
            {
                "ImageId": "ami-any",
                "Name": "any-ami",
                "State": "available",
                "CreationDate": "2025-01-15T00:00:00.000Z",
                "Tags": [],
            }
        ]
    }
    mock_session.return_value.client.return_value = mock_ec2

    result = list_amis(managed_only=False)

    assert len(result) == 1
    assert result[0].image_id == "ami-any"
    assert result[0].source_instance is None
    mock_ec2.describe_images.assert_called_once_with(Owners=["self"])


@patch("desk.aws.boto3.Session")
def test_get_ssm_command_uses_list_commands(mock_session: MagicMock) -> None:
    """get_ssm_command uses list_commands(CommandId=) when get_command is unavailable."""
    mock_ssm = MagicMock()
    mock_ssm.list_commands.return_value = {
        "Commands": [
            {
                "CommandId": "cid-1",
                "DocumentName": "AWS-RunShellScript",
                "Comment": "desk-ami-build:test:0:run",
                "Parameters": {"commands": ["echo hi"]},
            }
        ]
    }
    mock_session.return_value.client.return_value = mock_ssm

    cmd = get_ssm_command("cid-1", region="us-east-1", profile=None)

    assert cmd["DocumentName"] == "AWS-RunShellScript"
    assert cmd["Parameters"]["commands"] == ["echo hi"]
    mock_ssm.list_commands.assert_called_once_with(CommandId="cid-1", MaxResults=1)


@patch("desk.aws.boto3.Session")
def test_get_ssm_command_empty_raises(mock_session: MagicMock) -> None:
    """get_ssm_command raises when list_commands returns no rows."""
    mock_ssm = MagicMock()
    mock_ssm.list_commands.return_value = {"Commands": []}
    mock_session.return_value.client.return_value = mock_ssm

    with pytest.raises(RuntimeError, match="No SSM command found"):
        get_ssm_command("missing", region="us-east-1", profile=None)


@patch("desk.aws.boto3.Session")
def test_generate_presigned_get_object_url(mock_session: MagicMock) -> None:
    """generate_presigned_get_object_url delegates to S3 generate_presigned_url."""
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://bucket.s3.amazonaws.com/k?sig=1"
    mock_session.return_value.client.return_value = mock_s3

    url = generate_presigned_get_object_url("my-bucket", "path/key.txt", region="us-east-1", profile=None)

    assert url.startswith("https://")
    mock_s3.generate_presigned_url.assert_called_once()
    call_kw = mock_s3.generate_presigned_url.call_args
    assert call_kw[0][0] == "get_object"


@patch("desk.aws.boto3.Session")
def test_list_s3_object_keys_under_prefix(mock_session: MagicMock) -> None:
    """list_s3_object_keys_under_prefix skips trailing-slash keys."""
    mock_s3 = MagicMock()
    mock_s3.get_paginator.return_value.paginate.return_value = [
        {
            "Contents": [
                {"Key": "p/a/"},
                {"Key": "p/a/x.txt"},
                {"Key": "p/b/y.txt"},
            ]
        }
    ]
    mock_session.return_value.client.return_value = mock_s3

    keys = list_s3_object_keys_under_prefix("b", "p/", region="us-east-1", profile=None)

    assert keys == ["p/a/x.txt", "p/b/y.txt"]
