# desk

A CLI to manage EC2 instances as remote workstations. Workstations run in private subnets with no public IPs. Connect via SSH over AWS Systems Manager (SSM) Session Manager—no bastion hosts or exposed ports.

This repo is a **monorepo** with three subprojects:

| Project     | Description |
|------------|-------------|
| **desk-sdk**   | Shared library (AWS, config, keys, logging, tab/control workflows). Used by the CLI and by Lambdas. |
| **desk-cli**   | CLI application. Depends on desk-sdk. Provides the `desk` command. |
| **desk-infra** | CloudFormation templates and Lambda code (reaper). Depends on desk-sdk only (uv pip install). |

The root is a **uv** workspace that links desk-sdk and desk-cli.

---

## Installation

From the repo root:

```bash
uv sync
uv run desk --help
```

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

---

## Quick start

1. Deploy the desk VPC stack (see [Infrastructure](#infrastructure)).
2. Create and connect: `desk up`

No SSH key setup required: workstations are created without EC2 key pairs. When you connect, desk temporarily injects your local public key (~/.ssh/id_ed25519 or ~/.ssh/id_rsa) via SSM, then you SSH over the SSM tunnel. Default workstation name: `main`.

---

## Commands

| Command | Description |
|---------|-------------|
| `desk up` | Create a workstation (if none exists) and connect. Skips create if one is already running or pending. |
| `desk create` | Create a new workstation instance. |
| `desk connect` | SSH to a workstation over SSM. Waits for SSM agent if instance is still booting. |
| `desk keygen` | Generate an SSH key (~/.ssh/id_ed25519 by default) for use with connect/scp. |
| `desk list` | List workstations (instance ID, name, state, shutdown time). States are color-coded. |
| `desk start` | Start a stopped workstation. |
| `desk stop` | Stop a running workstation. |
| `desk kill` | Terminate a workstation (permanent). |
| `desk run` | Run a script on a workstation via SSM. |
| `desk scp` | Copy files to/from a workstation via SCP over SSM. |
| `desk auto-stop` | Set or change the auto-stop timer on a workstation. |
| `desk reap` | Stop all workstations past their auto-stop time. |
| `desk ami` | Manage AMIs: `list`, `build` (from recipe), `create` (from a running workstation). |
| `desk tab` | Manage screen sessions across disconnect/reconnect: `connect`, `list`, `create`, `close`. |

---

## Usage

**Create and connect (recommended):**

```bash
# Create if needed, then SSH (workstation name is required)
desk up main

# Custom name
desk up dev
```

**Create a workstation:**

```bash
# Workstation name is required (no EC2 key pair; connect uses key injection)
desk create main

# Custom options
desk create my-box --instance-type t3.large
```

**Connect (SSH over SSM):**

Desk temporarily adds your **public** key to the instance's `authorized_keys` via SSM (then removes it after `--key-timeout`, default 300s). It uses ~/.ssh/id_ed25519 or ~/.ssh/id_rsa by default, or the key at `-i PATH`.

```bash
# Workstation name or instance ID is required; key=~/.ssh/id_ed25519 or id_rsa by default
desk connect main
desk connect i-0abc123def456

# Custom identity file
desk connect main -i ~/.ssh/my-key
desk connect main --key-timeout 60   # remove injected key after 60s
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

**SSH key:** Create a key if you don't have one: `desk keygen` (creates ~/.ssh/id_ed25519 by default), or `desk keygen -f ~/.path/to/key`. Connect and scp use the default key; override with `-i PATH`.

**SSH config (optional):**

To use `ssh` directly with instance IDs:

```
Host i-* mi-*
  ProxyCommand sh -c "aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
  User ubuntu
  IdentityFile ~/.ssh/id_ed25519
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
- Workstations in a VPC with outbound internet via NAT Gateway
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

## Configuration

Optional config file: `~/.config/desk/config.ini` (or set `DESK_CONFIG` to your path). Copy from `desk-cli/config.example`.

AWS **region** and **credential profile** for API calls come from the environment (`AWS_REGION`, `AWS_PROFILE`, or `AWS_DEFAULT_REGION`) and from the config file (`region`, `aws_profile` in `[default]` or in `[profile NAME]` when a desk profile is active).

**Desk profiles:** the `[default]` section is your default desk profile (same idea as AWS `~/.aws/config`). Optional `[profile NAME]` sections hold alternate `region`, `aws_profile`, and `ami_prefix` for other accounts. To use one of those, set `DESK_PROFILE` or `desk --profile NAME` **before** the subcommand. Local state (routes, logs) is under `~/.local/state/desk/<NAME>/` when a named desk profile is active.

**Region:** optional in each section; set it when the AWS profile does not define a region in `~/.aws/config`, or to override the profile’s default region for desk.

```ini
[default]
region = us-east-1
aws_profile = my-aws-profile
ami_prefix = my-desk-ami   ; default AMI name prefix when creating workstations without --ami (tested AMIs only; see below)

[profile work]
region = eu-west-1
aws_profile = work-aws
ami_prefix = desk-ami-work
```

With `ami_prefix` set, `desk create` picks the newest matching AMI that is tagged `desk:ami-build-status=tested`. To allow any matching image (for example before tests have been run), use `desk create --allow-untested-ami`. The API accepts the same flag as `allow_untested_ami` on `POST /api/workstations`. AMI builds (`desk ami build`) use a JSON recipe with a `build` section (and optional `test` section); see `desk ami build run --help`.

---

## Infrastructure

VPC and reaper Lambda are in **desk-infra**. See [desk-infra/README.md](desk-infra/README.md) for how to deploy the CloudFormation stacks.

---

## Auto-stop

Workstations are tagged with a `desk:shutdown-at` time (default: 4 hours after start). This is set automatically on `desk create`, `desk start`, and `desk up`.

```bash
# Configure shutdown duration on create/start/up
desk create --shutdown 8h
desk start main --shutdown 2h30m
desk up --shutdown 30m
desk up --shutdown 0           # disable auto-stop

# Change the timer on a running workstation
desk auto-stop main 6h         # reset to 6h from now
desk auto-stop main 30m        # 30 minutes from now
desk auto-stop main --clear    # remove timer

# Manually stop overdue instances (also runs automatically via Lambda)
desk reap                      # stop all overdue
desk reap --dry-run            # preview without stopping
```

`desk list` shows a SHUTDOWN column with relative times. Overdue instances show in red.

---

## Development

```bash
uv sync --extra dev
./run_tests.sh   # run SDK and CLI tests (two pytest invocations to avoid conftest conflict)
```

Or run each suite separately: `uv run pytest desk-sdk/tests -q` and `uv run pytest desk-cli/tests -q`.

---

## Planned features

| Feature | Description |
|---------|-------------|
| **Interactive selection** | Pick from multiple workstations when name is ambiguous. |
