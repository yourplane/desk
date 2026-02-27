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

## Configuration

Optional config file: `~/.config/desk/config.ini` (or set `DESK_CONFIG` to your path). Copy from `config.example`. Overrides: `--region` / `--profile` or `AWS_REGION` / `AWS_PROFILE`.

```ini
[defaults]
region = us-east-1
profile = my-aws-profile
ami_prefix = my-desk-ami   ; default AMI name prefix when creating workstations without --ami
```

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

### Auto-stop reaper Lambda

The reaper is a Lambda that runs every 10 minutes and stops workstations past their `desk:shutdown-at` time. It uses the same `desk` library code as the CLI.

Requires [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

**Build and deploy:** From the repo root, `infrastructure/build.sh` copies the desk package into the reaper and runs `sam build`:

```bash
./infrastructure/build.sh
cd infrastructure
sam deploy --guided --capabilities CAPABILITY_IAM --stack-name desk-reaper
```

On subsequent deploys (after the guided config is saved to `samconfig.toml`):

```bash
./infrastructure/build.sh
cd infrastructure
sam deploy
```

### Desk control plane Lambda

The **desk-control** Lambda runs all desk control-plane operations (e.g. `desk list`, `desk start`, `desk stop`, `desk create`, `desk ami list`, `desk tab list`). It does **not** support interactive commands: `desk connect` and `desk scp` are excluded (they require SSH). Invoke it with an event that specifies the command and arguments.

**Build:** From the repo root, `infrastructure/build.sh` copies the desk package into both the reaper and control Lambda directories and runs `sam build` for both templates:

```bash
./infrastructure/build.sh
```

This builds:
- `desk-reaper.yaml` → reaper Lambda
- `desk-control.yaml` → control plane Lambda

**Deploy the control Lambda:** Build first (so the built template and artifacts are in `.aws-sam/build/`), then deploy using the **built** template so the Lambda package includes all dependencies:

```bash
./infrastructure/build.sh
cd infrastructure
sam deploy --guided --template-file .aws-sam/build/template.yaml --stack-name desk-control --capabilities CAPABILITY_IAM --region us-east-1
```

Subsequent deploys (after `samconfig.toml` is updated for the control stack):

```bash
./infrastructure/build.sh
cd infrastructure
sam deploy --template-file .aws-sam/build/template.yaml --stack-name desk-control --capabilities CAPABILITY_IAM --region us-east-1
```

Note: Deploy the built template (`.aws-sam/build/template.yaml`), not the source `desk-control.yaml`, so the deployed function uses the built artifact that includes the desk package and dependencies.

**Invoke:** Send an event with `argv` (list of CLI args) or `command`/`args`/`options`. Optional `env` sets environment variables (e.g. `AWS_REGION`, `AWS_PROFILE`).

```bash
# List workstations
aws lambda invoke --function-name desk-control --payload '{"argv": ["list"]}' out.json && cat out.json

# Start a workstation
aws lambda invoke --function-name desk-control --payload '{"argv": ["start", "main", "--region", "us-east-1"]}' out.json && cat out.json

# Using command/args/options
aws lambda invoke --function-name desk-control --payload '{"command": "stop", "args": ["main"], "env": {"AWS_REGION": "us-east-1"}}' out.json && cat out.json
```

Response shape: success `{"result": <data>}` (e.g. `list` → `{"result": {"workstations": [{"instance_id": "...", "name": "...", "state": "...", "shutdown_at": "..."}]}}`); failure `{"error": "..."}`. All responses are JSON; no CLI table output.

**Allowed commands:** `list`, `start`, `stop`, `up`, `create`, `kill`, `reap`, `auto-stop`, `run`, `ami` (all subcommands), `tab list`, `tab create`, `tab close`. Not allowed: `connect`, `scp`, `tab connect`, `tab up`, `keygen`.

**Lint CloudFormation:**
```bash
tox run -e lint
```

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
pip install -e ".[dev]"
tox run -e py    # tests
```

---

## Planned features

| Feature | Description |
|---------|-------------|
| **Interactive selection** | Pick from multiple workstations when name is ambiguous. |
