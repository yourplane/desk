"""AWS API helpers for desk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import boto3

from desk.log import get_logger

log = get_logger("aws")
from botocore.exceptions import ClientError

# Tag key used to store the scheduled shutdown time (ISO 8601 UTC).
TAG_SHUTDOWN_AT = "desk:shutdown-at"


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


def get_latest_ami_by_name_prefix(
    prefix: str,
    region: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Get the latest owned AMI whose name starts with the given prefix. Returns None if no match."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    response = ec2.describe_images(Owners=["self"], Filters=[{"Name": "state", "Values": ["available"]}])
    images = [
        img
        for img in response.get("Images", [])
        if (img.get("Name") or "").startswith(prefix)
    ]
    if not images:
        return None
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
    key_name: str | None = None,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Launch an EC2 instance and return its instance ID."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    run_kw: dict = {
        "ImageId": ami_id,
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "SubnetId": subnet_id,
        "SecurityGroupIds": security_group_ids,
        "IamInstanceProfile": {"Name": iam_instance_profile_name},
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "Type", "Value": "workstation"},
                    {"Key": "desk:managed", "Value": "true"},
                ],
            },
        ],
        "MetadataOptions": {
            "HttpTokens": "optional",
        },
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "VolumeSize": 32,
                    "VolumeType": "gp3",
                    "DeleteOnTermination": True,
                },
            },
        ],
    }
    if key_name:
        run_kw["KeyName"] = key_name

    response = ec2.run_instances(**run_kw)

    instance_id = response["Instances"][0]["InstanceId"]
    return instance_id


@dataclass
class Workstation:
    """EC2 instance identified as a desk workstation."""

    instance_id: str
    name: str
    state: str
    shutdown_at: str | None = None


def list_workstations(
    region: str | None = None,
    profile: str | None = None,
    *,
    states: list[str] | None = None,
) -> list[Workstation]:
    """List EC2 instances tagged Type=workstation. Optionally filter by state(s)."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    workstations: list[Workstation] = []
    filters = [{"Name": "tag:Type", "Values": ["workstation"]}]
    if states:
        filters.append({"Name": "instance-state-name", "Values": states})

    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate(Filters=filters):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                workstations.append(
                    Workstation(
                        instance_id=instance["InstanceId"],
                        name=tags.get("Name", ""),
                        state=instance["State"]["Name"],
                        shutdown_at=tags.get(TAG_SHUTDOWN_AT),
                    )
                )

    return workstations


def resolve_workstation(
    name_or_id: str,
    region: str | None = None,
    profile: str | None = None,
    *,
    states: list[str] | None = None,
) -> str:
    """Resolve workstation name or instance ID to instance ID. Raises ValueError if not found.

    When resolving by name, considers instances in the given states (default:
    running and pending). Errors if multiple instances share the same name.
    """
    if states is None:
        states = ["running", "pending"]

    if name_or_id.startswith("i-"):
        workstations = list_workstations(region=region, profile=profile)
        for w in workstations:
            if w.instance_id == name_or_id:
                return w.instance_id
        raise ValueError(f"Workstation '{name_or_id}' not found. Run 'desk list' to see workstations.")

    # Resolve by name with given states filter
    matching_state = list_workstations(
        region=region, profile=profile, states=states
    )
    matches = [w for w in matching_state if w.name == name_or_id]
    if len(matches) > 1:
        ids = ", ".join(m.instance_id for m in matches)
        raise ValueError(
            f"Multiple workstations named '{name_or_id}': {ids}. "
            "Use the instance ID to connect to a specific one."
        )
    if len(matches) == 1:
        return matches[0].instance_id

    raise ValueError(f"Workstation '{name_or_id}' not found. Run 'desk list' to see workstations.")


def is_ssm_ready(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> bool:
    """Check if instance is registered with SSM and ready for Session Manager."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ssm = session.client("ssm")
    try:
        resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
        )
        infos = resp.get("InstanceInformationList", [])
        log.debug("describe_instance_information instance_id=%s result_count=%d", instance_id, len(infos))
        for info in infos:
            pid = info.get("InstanceId")
            status = info.get("PingStatus")
            log.debug("instance %s PingStatus=%s", pid, status)
            if pid == instance_id and status == "Online":
                log.debug("instance %s is SSM ready", instance_id)
                return True
        log.debug("instance %s not in SSM or not Online", instance_id)
        return False
    except Exception as e:
        log.debug("describe_instance_information failed instance_id=%s error=%s", instance_id, e)
        return False


def wait_for_ssm_ready(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
    timeout: int = 300,
    poll_interval: float = 3.0,
) -> bool:
    """
    Wait for instance to become ready for SSM. Returns True if ready, False if timeout.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_ssm_ready(instance_id, region=region, profile=profile):
            return True
        time.sleep(poll_interval)
    return False


def list_ec2_key_pairs(
    region: str | None = None,
    profile: str | None = None,
) -> set[str]:
    """Return set of EC2 key pair names in the region."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    resp = ec2.describe_key_pairs()
    return {kp["KeyName"] for kp in resp.get("KeyPairs", [])}


def get_running_workstations_using_key(
    key_name: str,
    region: str | None = None,
    profile: str | None = None,
) -> list[str]:
    """Return instance IDs of running workstations that use this key pair."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    paginator = ec2.get_paginator("describe_instances")
    instance_ids: list[str] = []

    for page in paginator.paginate(
        Filters=[
            {"Name": "tag:Type", "Values": ["workstation"]},
            {"Name": "key-name", "Values": [key_name]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ],
    ):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_ids.append(instance["InstanceId"])

    return instance_ids


def delete_key_pair(
    key_name: str,
    region: str | None = None,
    profile: str | None = None,
) -> None:
    """Delete an EC2 key pair."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.delete_key_pair(KeyName=key_name)


def create_key_pair(
    key_name: str,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """
    Create an EC2 key pair. Returns the private key material (PEM).
    Caller is responsible for saving it securely.
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    resp = ec2.create_key_pair(KeyName=key_name)
    return resp["KeyMaterial"]


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


def start_instance(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Start a stopped EC2 instance. Returns the instance ID."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.start_instances(InstanceIds=[instance_id])
    return instance_id


def terminate_instance(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """Terminate an EC2 instance. Returns the instance ID."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.terminate_instances(InstanceIds=[instance_id])
    return instance_id


def get_instance_state(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Get the current state of an EC2 instance. Returns None if not found."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    reservations = resp.get("Reservations", [])
    if not reservations:
        return None
    instances = reservations[0].get("Instances", [])
    if not instances:
        return None
    return instances[0]["State"]["Name"]


def wait_for_instance_state(
    instance_id: str,
    target_state: str,
    region: str | None = None,
    profile: str | None = None,
    timeout: int = 300,
    poll_interval: float = 3.0,
) -> bool:
    """Wait for an instance to reach the target state. Returns True if reached, False if timeout."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_instance_state(instance_id, region=region, profile=profile)
        log.debug("wait_for_instance_state instance_id=%s current=%s target=%s", instance_id, state, target_state)
        if state == target_state:
            return True
        time.sleep(poll_interval)
    return False


@dataclass
class CommandResult:
    """Result of an SSM command invocation."""

    command_id: str
    status: str
    stdout: str
    stderr: str
    exit_code: int | None


def send_ssm_command(
    instance_id: str,
    command: str,
    region: str | None = None,
    profile: str | None = None,
    timeout_seconds: int = 3600,
) -> str:
    """
    Send a command to an instance via SSM. Returns the command ID.
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ssm = session.client("ssm")

    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
        TimeoutSeconds=timeout_seconds,
    )

    command_id = response["Command"]["CommandId"]
    log.debug("send_ssm_command instance_id=%s command_id=%s", instance_id, command_id)
    return command_id


def add_temporary_ssh_key(
    instance_id: str,
    user: str,
    public_key_content: str,
    timeout_seconds: int = 300,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """
    Add a public key to the instance's authorized_keys via SSM; remove it after
    timeout_seconds. The command runs on the instance (add key, sleep, remove key).
    Returns the SSM command ID. Caller should allow a second or two for the key
    to be written before starting SSH.
    """
    # One line in authorized_keys: "<key> ssm-session" so we can remove it later
    line = (public_key_content.strip() + " ssm-session").replace("'", "'\"'\"'")
    script = f"""set -eu
mkdir -p ~{user}/.ssh && chown {user}:{user} ~{user}/.ssh 2>/dev/null || true
cd ~{user}/.ssh || exit 1
authorized_key='{line}'
echo "${{authorized_key}}" >> authorized_keys
chown {user}:{user} authorized_keys 2>/dev/null || true
chmod 600 authorized_keys 2>/dev/null || true
sleep {timeout_seconds}
(grep -v -F "${{authorized_key}}" authorized_keys || true) > authorized_keys~
mv authorized_keys~ authorized_keys
"""
    return send_ssm_command(
        instance_id,
        script,
        region=region,
        profile=profile,
        timeout_seconds=timeout_seconds + 60,
    )


@dataclass
class AmiInfo:
    """Information about an AMI."""

    image_id: str
    name: str
    state: str
    source_instance_id: str | None = None


def create_ami(
    instance_id: str,
    name: str,
    description: str | None = None,
    no_reboot: bool = False,
    region: str | None = None,
    profile: str | None = None,
) -> str:
    """
    Create an AMI from an EC2 instance. Returns the AMI ID.

    Args:
        instance_id: The instance to create an AMI from
        name: Name for the AMI
        description: Optional description for the AMI
        no_reboot: If True, don't reboot the instance before creating the image
        region: AWS region
        profile: AWS profile
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    create_kw: dict = {
        "InstanceId": instance_id,
        "Name": name,
        "TagSpecifications": [
            {
                "ResourceType": "image",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "desk:managed", "Value": "true"},
                    {"Key": "desk:source-instance", "Value": instance_id},
                ],
            },
        ],
    }
    if description:
        create_kw["Description"] = description
    if no_reboot:
        create_kw["NoReboot"] = True

    response = ec2.create_image(**create_kw)
    image_id = response["ImageId"]
    log.debug("create_ami instance_id=%s image_id=%s name=%s", instance_id, image_id, name)
    return image_id


def get_ami_state(
    image_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Get the current state of an AMI. Returns None if not found."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    try:
        response = ec2.describe_images(ImageIds=[image_id])
        images = response.get("Images", [])
        if not images:
            return None
        return images[0]["State"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidAMIID.NotFound":
            return None
        raise


@dataclass
class AmiInfo:
    """Desk-managed AMI summary for list output."""

    image_id: str
    name: str
    state: str
    creation_date: str
    source_instance: str | None


def list_amis(
    region: str | None = None,
    profile: str | None = None,
    managed_only: bool = True,
) -> list[AmiInfo]:
    """
    List AMIs. By default returns only AMIs tagged desk:managed=true (created by desk).
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")

    params: dict = {"Owners": ["self"]}
    if managed_only:
        params["Filters"] = [{"Name": "tag:desk:managed", "Values": ["true"]}]

    response = ec2.describe_images(**params)
    images = response.get("Images", [])

    def _tag(img: dict, key: str) -> str | None:
        for t in img.get("Tags", []):
            if t.get("Key") == key:
                return t.get("Value")
        return None

    result: list[AmiInfo] = []
    for img in images:
        result.append(
            AmiInfo(
                image_id=img["ImageId"],
                name=img.get("Name", "-"),
                state=img.get("State", "unknown"),
                creation_date=img.get("CreationDate", ""),
                source_instance=_tag(img, "desk:source-instance"),
            )
        )

    result.sort(key=lambda a: a.creation_date, reverse=True)
    return result


def wait_for_ami_available(
    image_id: str,
    region: str | None = None,
    profile: str | None = None,
    timeout: int = 1200,
    poll_interval: float = 10.0,
) -> bool:
    """
    Wait for an AMI to become available. Returns True if available, False if timeout.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_ami_state(image_id, region=region, profile=profile)
        log.debug("wait_for_ami_available image_id=%s state=%s", image_id, state)
        if state == "available":
            return True
        if state in ("failed", "error", "deregistered"):
            return False
        time.sleep(poll_interval)
    return False


def get_command_invocation(
    command_id: str,
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> CommandResult:
    """
    Get the status and output of an SSM command invocation.
    """
    session = boto3.Session(region_name=region, profile_name=profile)
    ssm = session.client("ssm")

    response = ssm.get_command_invocation(
        CommandId=command_id,
        InstanceId=instance_id,
    )

    status = response["Status"]
    stdout = response.get("StandardOutputContent", "")
    stderr = response.get("StandardErrorContent", "")

    # ResponseCode is only present when command has finished
    exit_code = None
    if "ResponseCode" in response:
        exit_code = response["ResponseCode"]

    log.debug(
        "get_command_invocation command_id=%s instance_id=%s status=%s exit_code=%s",
        command_id,
        instance_id,
        status,
        exit_code,
    )

    return CommandResult(
        command_id=command_id,
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )


def parse_duration(value: str) -> float:
    """Parse a human duration string into hours.

    Accepted formats:
      4h       → 4.0
      30m      → 0.5
      2h30m    → 2.5
      4        → 4.0  (bare number treated as hours)
      0        → 0.0

    Raises ValueError on unrecognised input.
    """
    import re

    value = value.strip()

    # Bare number → hours
    try:
        return float(value)
    except ValueError:
        pass

    pattern = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?$", re.IGNORECASE)
    m = pattern.match(value)
    if not m or (m.group(1) is None and m.group(2) is None):
        raise ValueError(
            f"Invalid duration '{value}'. Use e.g. 4h, 30m, 2h30m, or a bare number (hours)."
        )
    hours = float(m.group(1)) if m.group(1) else 0.0
    minutes = float(m.group(2)) if m.group(2) else 0.0
    return hours + minutes / 60.0


def compute_shutdown_at(hours: float) -> str:
    """Return an ISO 8601 UTC timestamp *hours* from now."""
    from datetime import timedelta

    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def set_shutdown_tag(
    instance_id: str,
    shutdown_at: str,
    region: str | None = None,
    profile: str | None = None,
) -> None:
    """Set (or update) the desk:shutdown-at tag on an instance."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.create_tags(
        Resources=[instance_id],
        Tags=[{"Key": TAG_SHUTDOWN_AT, "Value": shutdown_at}],
    )
    log.debug("set_shutdown_tag instance_id=%s shutdown_at=%s", instance_id, shutdown_at)


def clear_shutdown_tag(
    instance_id: str,
    region: str | None = None,
    profile: str | None = None,
) -> None:
    """Remove the desk:shutdown-at tag from an instance."""
    session = boto3.Session(region_name=region, profile_name=profile)
    ec2 = session.client("ec2")
    ec2.delete_tags(
        Resources=[instance_id],
        Tags=[{"Key": TAG_SHUTDOWN_AT}],
    )
    log.debug("clear_shutdown_tag instance_id=%s", instance_id)


def reap_overdue(
    region: str | None = None,
    profile: str | None = None,
    *,
    dry_run: bool = False,
) -> list[Workstation]:
    """Find and stop workstations past their auto-stop time.

    Returns the list of overdue workstations (stopped, or would-be-stopped
    if dry_run is True).
    """
    now = datetime.now(timezone.utc)

    workstations = list_workstations(
        region=region, profile=profile, states=["running", "pending"]
    )

    overdue: list[Workstation] = []
    for w in workstations:
        if not w.shutdown_at:
            continue
        try:
            dt = datetime.strptime(w.shutdown_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            log.warning("bad shutdown-at tag on %s: %s", w.instance_id, w.shutdown_at)
            continue
        if dt <= now:
            overdue.append(w)

    if not dry_run:
        for w in overdue:
            log.info("stopping overdue instance %s (%s)", w.name, w.instance_id)
            stop_instance(w.instance_id, region=region, profile=profile)

    return overdue
