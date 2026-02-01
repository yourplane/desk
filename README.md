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
| **Key management** | Create, list, and delete keys via `desk key create/list/delete`. Keys stored in `~/.config/desk/keys/`. |

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

## Key management (Planned)

Desk manages SSH keys in a dedicated folder (`~/.config/desk/keys/`). Keys created with `desk key create` are stored there and registered in AWS for use with workstations.

| Command | Description |
|---------|-------------|
| `desk key create <name>` | Create a new key pair. Saves the private key to `~/.config/desk/keys/<name>.pem` and creates the EC2 key pair in AWS. |
| `desk key list` | List keys. Shows local keys (in the desk keys folder) and remote keys (EC2 key pairs in AWS), with indicators for which exist where. |
| `desk key delete <name>` | Delete a key. Removes the local `.pem` file and the EC2 key pair from AWS. Fails if any running workstation uses the key. |

**Usage (planned):**

```bash
# Create a key (stored in ~/.config/desk/keys/my-key.pem)
desk key create my-key

# List keys (shows local + remote status)
desk key list

# Delete a key
desk key delete my-key
```

When using desk-managed keys, `desk create --key-name my-key` and `desk connect my-workstation -i <path>` can resolve the key path from the desk keys folder by name, so you can use `desk connect my-workstation --key my-key` instead of `-i ~/.config/desk/keys/my-key.pem`.

---

## Usage

```bash
# Key management
desk key create my-key
desk key list
desk key delete my-key

# List all workstations
desk list

# Create a workstation (include --key-name for SSH access)
desk create --name my-workstation --key-name my-key

# Connect (SSH over SSM—private subnets only)
desk connect my-workstation -i ~/.config/desk/keys/my-key.pem
desk connect my-workstation --key my-key   # when using desk-managed keys

# Start and stop
desk start my-workstation
desk stop my-workstation
```

---

## Requirements

- Python 3.x
- AWS credentials configured (profile, env vars, or instance role)
- [SSM Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for `desk connect`
- SSH client (used for the SSH-over-SSM session)
- Workstations in a VPC with SSM VPC endpoints (or NAT) so instances can reach the SSM service
- IAM instance profile with `AmazonSSMManagedInstanceCore` on workstation instances

### Session Manager plugin

`desk connect` uses the Session Manager plugin to tunnel SSH through SSM. Install it once before connecting:

**Linux (Debian/Ubuntu):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o session-manager-plugin.deb
sudo dpkg -i session-manager-plugin.deb
```

**Linux (Amazon Linux 2, RHEL):**
```bash
sudo yum install -y https://s3.amazonaws.com/session-manager-downloads/plugin/latest/linux_64bit/session-manager-plugin.rpm
```

**macOS:**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/session-manager-plugin.pkg" -o session-manager-plugin.pkg
sudo installer -pkg session-manager-plugin.pkg -target /
```
(Use `mac_64bit` instead of `mac_arm64` for Intel Macs.)

**Verify:** `session-manager-plugin --version`

See the [AWS documentation](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for Windows and other platforms.

### SSH keys

`desk connect` needs an SSH key to authenticate to the instance. Ubuntu AMIs expect the key associated when the instance was launched.

**Desk-managed keys (recommended)**

Keys created with `desk key create` are stored in `~/.config/desk/keys/` and kept in sync with EC2. See [Key management](#key-management-planned) above.

```bash
desk key create my-key
desk create --name my-workstation --key-name my-key
desk connect my-workstation --key my-key
```

**Manual setup (existing keys)**

If you prefer to manage keys yourself or use an existing EC2 key pair:

```bash
# Create key pair in AWS, save private key
aws ec2 create-key-pair --key-name desk-key --query 'KeyMaterial' --output text > ~/.ssh/desk-key.pem
chmod 600 ~/.ssh/desk-key.pem

# Create workstation and connect
desk create --name my-workstation --key-name desk-key
desk connect my-workstation -i ~/.ssh/desk-key.pem
```

**SSH config (optional)**

To use `ssh` directly without `desk connect`, add to `~/.ssh/config`:

```
Host i-* mi-*
  ProxyCommand sh -c "aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
  User ubuntu
  IdentityFile ~/.config/desk/keys/my-key.pem
```

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
