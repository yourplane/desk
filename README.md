# desk

A CLI to manage EC2 instances as remote workstations. Workstations run in private subnets with no public IPs. Connect via SSH over AWS Systems Manager (SSM) Session Manager—no bastion hosts or exposed ports.

---

## Installation

```bash
pip install .
# or for isolation: pipx install .
```

Requires Python 3.10+.

---

## Quick start

1. Deploy the desk VPC stack (see [Infrastructure](#infrastructure)).
2. Create a key: `desk key create main-key` (or let `desk create` prompt you).
3. Create and connect: `desk up`

Defaults: workstation name `main`, key `main-key`. Run `desk up` with no arguments to create (if needed) and SSH in.

---

## Commands

| Command | Description |
|---------|-------------|
| `desk up` | Create a workstation (if none exists) and connect. Skips create if one is already running or pending. |
| `desk create` | Create a new workstation instance. |
| `desk connect` | SSH to a workstation over SSM. Waits for SSM agent if instance is still booting. |
| `desk list` | List workstations (instance ID, name, state). States are color-coded. |
| `desk stop` | Stop a running workstation. |
| `desk key create/list/delete` | Manage SSH keys in `~/.config/desk/keys/`. |

---

## Usage

**Create and connect (recommended):**

```bash
# Create if needed, then SSH (defaults: main, main-key)
desk up

# Custom name and key
desk up --name dev --key dev-key
```

**Create a workstation:**

```bash
# Defaults: name=main, key=main-key
desk create

# Custom options
desk create --name my-box --key my-key --instance-type t3.large
```

If `main-key` does not exist, `desk create` will prompt to create it.

**Connect (SSH over SSM):**

```bash
# Defaults: workstation=main, key=main-key
desk connect

# By name or instance ID
desk connect main
desk connect i-0abc123def456

# Custom key
desk connect --key my-key
desk connect -i ~/.ssh/my-key.pem
```

**List workstations:**

```bash
desk list          # Table format with colored states
desk list -o plain # Plain output (tab-separated)
```

**Stop a workstation:**

```bash
desk stop main
desk stop i-0abc123def456
```

---

## Key management

Keys created with `desk key create` are stored in `~/.config/desk/keys/` and registered in AWS.

| Command | Description |
|---------|-------------|
| `desk key create <name>` | Create key pair. Saves `~/.config/desk/keys/<name>.pem` and creates EC2 key pair in AWS. |
| `desk key list` | List keys with local/remote status. |
| `desk key delete <name>` | Remove local file and EC2 key pair. Prompts for confirmation. Fails if key is used by running workstations (override with `--force`). |

**Desk-managed keys (recommended):**

```bash
desk key create main-key
desk create    # uses main-key by default
desk connect   # uses main-key by default
```

**Manual setup (existing keys):**

```bash
aws ec2 create-key-pair --key-name desk-key --query 'KeyMaterial' --output text > ~/.ssh/desk-key.pem
chmod 600 ~/.ssh/desk-key.pem
desk create --key desk-key
desk connect -i ~/.ssh/desk-key.pem
```

**SSH config (optional):**

Add to `~/.ssh/config` to use `ssh` directly:

```
Host i-* mi-*
  ProxyCommand sh -c "aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
  User ubuntu
  IdentityFile ~/.config/desk/keys/main-key.pem
```

---

## Logs

Debug logs for troubleshooting (e.g. `desk connect` stuck waiting):

**Location:** `~/.config/desk/desk.log` (or `$XDG_CONFIG_HOME/desk/desk.log`)

```bash
tail -f ~/.config/desk/desk.log
```

---

## Requirements

- Python 3.10+
- AWS credentials (profile, env vars, or instance role)
- [SSM Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- SSH client
- Workstations in a VPC with SSM VPC endpoints (or NAT)
- IAM instance profile with `AmazonSSMManagedInstanceCore` on workstation instances

### Session Manager plugin

`desk connect` tunnels SSH through SSM. Install the plugin once:

**Linux (Debian/Ubuntu):**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o session-manager-plugin.deb
sudo dpkg -i session-manager-plugin.deb
```

**macOS:**
```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/session-manager-plugin.pkg" -o session-manager-plugin.pkg
sudo installer -pkg session-manager-plugin.pkg -target /
```
(Use `mac_64bit` for Intel Macs.)

**Verify:** `session-manager-plugin --version`

See [AWS docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for other platforms.

---

## Infrastructure

Deploy the desk VPC and networking:

```bash
aws cloudformation deploy \
  --stack-name desk \
  --template-file infrastructure/desk-vpc.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```

The template provides:

- **VPC** with private subnets (2 AZs)
- **VPC endpoints** for SSM (`ec2messages`, `ssm`, `ssmmessages`) and S3
- **NAT Gateway** for outbound internet
- **Security group** for workstations (no inbound rules)
- **IAM instance profile** (`AmazonSSMManagedInstanceCore`)

`desk create` uses the stack outputs automatically.

**Lint CloudFormation:**
```bash
tox run -e lint
```

---

## Development

```bash
pip install -e ".[dev]"
tox run -e py    # tests
```

---

## Planned features

| Feature | Description |
|---------|-------------|
| **desk start** | Start stopped workstations. |
| **Config file** | User config for default profile, region. |
| **Interactive selection** | Pick from multiple workstations when name is ambiguous. |
