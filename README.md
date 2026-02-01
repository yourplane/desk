# desk

A CLI utility to manage EC2 instances as remote workstations. All workstations live in private subnets with no public IPs. Connect via `desk connect`—SSH over AWS Systems Manager (SSM) Session Manager, so no bastion hosts or exposed ports.

---

## Existing Features

_None yet. This project is in initial development._

---

## Planned Features

### Core

| Feature | Description |
|---------|-------------|
| **List workstations** | List EC2 instances tagged/identified as workstations. Show instance ID, name, state, and connection info. |
| **Connect** | Connect to a workstation by instance ID, name, or alias. Uses SSH over SSM—tunnels your SSH session through Session Manager, the only supported method (all workstations are in private subnets). |
| **Start / Stop instances** | Start stopped workstations and stop running ones to control costs. |

### Configuration

| Feature | Description |
|---------|-------------|
| **Config file** | User config (e.g. `~/.config/desk/config.yaml`) for default AWS profile, region, and workstation filters. |
| **Workstation identification** | Define which instances are “workstations” via tags (e.g. `Type=workstation`), instance name patterns, or explicit instance IDs. |
| **AWS profile / region** | Support `--profile` and `--region` flags and config defaults. Respect `AWS_PROFILE` and `AWS_REGION` env vars. |

### Quality of Life

| Feature | Description |
|---------|-------------|
| **Interactive selection** | When multiple workstations match, prompt to pick one (e.g. fuzzy finder or numbered list). |
| **Aliases** | Short names for workstations (e.g. `dev-box`, `gpu-node`) mapped to instance IDs. |
| **Status / health** | Quick status check: instance state, SSM agent status, and basic connectivity info. |
| **Wait for ready** | After starting an instance, optionally wait until connectivity is available before connecting. |

### Nice to Have

| Feature | Description |
|---------|-------------|
| **Launch templates** | Create new workstation instances from saved launch templates. |
| **Cost estimates** | Show approximate hourly/monthly cost for running workstations. |
| **Session history** | Log connection attempts and durations for auditing. |

---

## Usage (Planned)

```bash
# List all workstations
desk list

# Connect (SSH over SSM—private subnets only)
desk connect my-workstation
desk connect i-0abc123def456
desk connect --user ubuntu dev-box

# Start and stop
desk start my-workstation
desk stop my-workstation

# Wait for ready, then connect
desk start my-workstation --wait
desk connect my-workstation --wait
```

---

## Requirements

- Python 3.x
- AWS credentials configured (profile, env vars, or instance role)
- [SSM Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for `desk connect`
- SSH client (used for the SSH-over-SSM session)
- Workstations in a VPC with SSM VPC endpoints (or NAT) so instances can reach the SSM service
- IAM instance profile with `AmazonSSMManagedInstanceCore` on workstation instances

---

## Infrastructure

A CloudFormation template in `infrastructure/desk-vpc.yaml` creates the VPC and networking for desk:

- **VPC** with private subnets (2 AZs) for workstations
- **VPC endpoints** for SSM (`ec2messages`, `ssm`, `ssmmessages`) and S3 — instances reach AWS without NAT for SSM
- **NAT Gateway** for outbound internet (package installs, git)
- **Security group** for workstations — no inbound rules (SSM uses outbound)
- **IAM instance profile** with `AmazonSSMManagedInstanceCore` for workstations

**Deploy:**

```bash
aws cloudformation deploy
    --stack-name desk
    --template-file infrastructure/desk-vpc.yaml
    --capabilities CAPABILITY_NAMED_IAM
```

Use the exported outputs (`VpcId`, `PrivateSubnetIds`, `WorkstationSecurityGroupId`, `WorkstationInstanceProfile`) when launching workstation instances.

**Build / lint (tox):**

```bash
pip install tox
tox run -e lint
```

---

## Installation (Planned)

`pip install desk` (or `pipx install desk`)
