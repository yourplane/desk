"""AWS API helpers for desk."""

from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError


@dataclass
class DeskVpcOutputs:
    """CloudFormation stack outputs for desk-vpc."""

    vpc_id: str
    private_subnet_ids: list[str]
    security_group_id: str
    instance_profile_name: str


def get_desk_vpc_outputs(
    stack_name: str = "desk",
    region: str | None = None,
    profile: str | None = None,
) -> DeskVpcOutputs:
    """Fetch desk-vpc CloudFormation stack outputs."""
    session = boto3.Session(region_name=region, profile_name=profile)
    cf = session.client("cloudformation")
    resolved_region = session.region_name

    try:
        response = cf.describe_stacks(StackName=stack_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            region_hint = f" (region: {resolved_region})" if resolved_region else ""
            profile_hint = f" (profile: {profile})" if profile else ""
            raise RuntimeError(
                f"Stack '{stack_name}' not found{region_hint}{profile_hint}.\n\n"
                "Possible causes:\n"
                "  • Wrong region  – try --region or set AWS_REGION\n"
                "  • Wrong profile – try --profile or set AWS_PROFILE\n"
                "  • Stack not deployed in this account\n\n"
                f"Verify:  aws cloudformation describe-stacks --stack-name {stack_name} --region <region>\n"
                f"Deploy:   aws cloudformation create-stack --stack-name {stack_name} "
                "--template-body file://infrastructure/desk-vpc.yaml"
            ) from e
        raise

    stack = response["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}

    subnet_ids = outputs.get("PrivateSubnetIds", "").split(",")
    if not subnet_ids or not subnet_ids[0]:
        raise RuntimeError(
            f"Stack '{stack_name}' has no PrivateSubnetIds output. "
            "Ensure the stack was deployed successfully."
        )

    return DeskVpcOutputs(
        vpc_id=outputs["VpcId"],
        private_subnet_ids=[s.strip() for s in subnet_ids],
        security_group_id=outputs["WorkstationSecurityGroupId"],
        instance_profile_name=outputs["WorkstationInstanceProfile"],
    )


def get_latest_ubuntu_ami(
    version: str = "24.04",
    architecture: str = "x86_64",
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Get the latest Ubuntu AMI ID for the given version and architecture."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    # Canonical's owner ID for Ubuntu. Name pattern: ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server-*
    # EC2 Architecture uses x86_64 but Ubuntu names use amd64
    name_arch = "amd64" if architecture == "x86_64" else architecture
    name_pattern = f"ubuntu/images/hvm-ssd*/ubuntu-noble-{version}-{name_arch}-server-*"

    response = ec2.describe_images(
        Owners=["099720109477"],
        Filters=[
            {"Name": "name", "Values": [name_pattern]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": [architecture]},
        ],
    )

    images = response.get("Images", [])
    if not images:
        # Fallback: broader pattern without version in name
        name_pattern = f"ubuntu/images/hvm-ssd*/ubuntu-noble-*-{name_arch}-server-*"
        response = ec2.describe_images(
            Owners=["099720109477"],
            Filters=[
                {"Name": "name", "Values": [name_pattern]},
                {"Name": "state", "Values": ["available"]},
            ],
        )
        images = response.get("Images", [])

    if not images:
        raise RuntimeError(
            f"No Ubuntu {version} {architecture} AMI found in this region. "
            "Check region or specify an AMI explicitly with --ami."
        )

    # Sort by creation date, newest first
    images.sort(key=lambda x: x.get("CreationDate", ""), reverse=True)
    return images[0]["ImageId"]


def run_instance(
    *,
    ami_id: str,
    instance_type: str,
    subnet_id: str,
    security_group_ids: list[str],
    iam_instance_profile_name: str,
    name: str,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Launch an EC2 instance and return its instance ID."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    response = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=security_group_ids,
        IamInstanceProfile={"Name": iam_instance_profile_name},
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "Type", "Value": "workstation"},
                    {"Key": "desk:managed", "Value": "true"},
                ],
            },
        ],
        MetadataOptions={
            "HttpTokens": "optional",  # IMDSv2 optional for broader compatibility
        },
    )

    instance_id = response["Instances"][0]["InstanceId"]
    return instance_id


@dataclass
class Workstation:
    """EC2 instance identified as a desk workstation."""

    instance_id: str
    name: str
    state: str


def list_workstations(
    region: str | None = None,
    profile: str | None = None,
) -> list[Workstation]:
    """List EC2 instances tagged Type=workstation."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    workstations: list[Workstation] = []
    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate(
        Filters=[
            {"Name": "tag:Type", "Values": ["workstation"]},
        ],
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                name = next(
                    (t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"),
                    "",
                )
                workstations.append(
                    Workstation(
                        instance_id=instance["InstanceId"],
                        name=name,
                        state=instance["State"]["Name"],
                    )
                )

    return workstations


def stop_instance(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Stop an EC2 instance. Returns the instance ID."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.stop_instances(InstanceIds=[instance_id])
    return instance_id
