# desk-infra

CloudFormation templates and Lambda functions for desk: VPC/networking, auto-stop reaper, and control-plane Lambda.

Requires [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) for building and deploying the Lambdas. Build uses `uv pip install` to install desk-sdk into the Lambda packages.

---

## Deploy CloudFormation stacks

Deploy in this order: VPC first, then the Lambdas.

### 1. VPC and networking

From the repo root:

```bash
aws cloudformation deploy \
  --stack-name desk \
  --template-file desk-infra/desk-vpc.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```

The template provides:

- **VPC** with private subnets (2 AZs)
- **NAT Gateway** for outbound internet (SSM and S3 traffic use the NAT)
- **Security group** for workstations (no inbound rules)
- **IAM instance profile** (`AmazonSSMManagedInstanceCore`)

`desk create` (CLI) uses the stack outputs automatically.

### 2. Reaper Lambda (auto-stop)

The reaper runs every 10 minutes and stops workstations past their `desk:shutdown-at` time.

Run these commands from the **repo root** (the directory that contains `desk-infra/`). The build script and `sam` deploy must be run from the correct directory; `--template-file` is required so SAM deploys the reaper template (not the control template).

First-time deploy (guided setup):

```bash
./desk-infra/build.sh
cd desk-infra
sam build --template desk-reaper.yaml
sam deploy --guided --template-file .aws-sam/build/template.yaml --stack-name reaper --capabilities CAPABILITY_IAM
```

On subsequent deploys (after the guided config is saved to `samconfig.toml`):

```bash
# From repo root
./desk-infra/build.sh
cd desk-infra
sam build --template desk-reaper.yaml
sam deploy --template-file .aws-sam/build/template.yaml --stack-name reaper --capabilities CAPABILITY_IAM
```

### 3. Control plane Lambda

The **desk-control** Lambda runs desk control-plane operations (e.g. list, start, stop, create, ami, tab list/create/close). It does **not** support interactive commands (`connect`, `scp`).

Run these commands from the **repo root** (the directory that contains `desk-infra/`). Build the control template before deploy so `.aws-sam/build/template.yaml` is the control stack (not the reaper).

First-time deploy (guided setup):

```bash
./desk-infra/build.sh
cd desk-infra
sam build --template desk-control.yaml
sam deploy --guided --template-file .aws-sam/build/template.yaml --stack-name desk-control --capabilities CAPABILITY_IAM --region us-east-1
```

Subsequent deploys (after `samconfig.toml` is updated for the control stack):

```bash
# From repo root
./desk-infra/build.sh
cd desk-infra
sam build --template desk-control.yaml
sam deploy --template-file .aws-sam/build/template.yaml --stack-name desk-control --capabilities CAPABILITY_IAM --region us-east-1
```

Deploy the **built** template (`.aws-sam/build/template.yaml`) so the Lambda package includes desk-sdk and dependencies.

---

## Invoke the control Lambda

Send an event with `argv` (list of CLI args) or `command`/`args`/`options`. Optional `env` sets environment variables (e.g. `AWS_REGION`, `AWS_PROFILE`).

Use **`--payload file://...`** so the JSON is not mangled by the shell (inline payloads can cause "Invalid UTF-8" or parse errors).

**Example: desk list**

```bash
echo '{"argv": ["list"]}' > payload.json
aws lambda invoke --function-name desk-control --payload file://payload.json out.json && cat out.json
```

Response: `{"result": {"workstations": [{"instance_id": "...", "name": "...", "state": "...", "shutdown_at": "..."}]}}` on success, or `{"error": "..."}` on failure.

**More examples:**

```bash
# Start a workstation
echo '{"argv": ["start", "main", "--region", "us-east-1"]}' > payload.json
aws lambda invoke --function-name desk-control --payload file://payload.json out.json && cat out.json

# Stop (using command/args/options)
echo '{"command": "stop", "args": ["main"], "env": {"AWS_REGION": "us-east-1"}}' > payload.json
aws lambda invoke --function-name desk-control --payload file://payload.json out.json && cat out.json
```

All responses are JSON. **Allowed commands:** `list`, `start`, `stop`, `up`, `create`, `kill`, `reap`, `auto-stop`, `run`, `ami` (all subcommands), `tab list`, `tab create`, `tab close`. Not allowed: `connect`, `scp`, `tab connect`, `tab up`, `keygen`.

---

## Lint CloudFormation

```bash
cfn-lint desk-infra/desk-vpc.yaml desk-infra/desk-reaper.yaml desk-infra/desk-control.yaml
```

Run from the repo root, or pass paths to the templates from your current directory.
