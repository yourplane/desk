# desk-infra

CloudFormation templates and Lambda functions for desk: VPC/networking and auto-stop reaper.

Requires [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) for building and deploying the Lambda. Build uses `uv pip install` to install desk-sdk into the Lambda package.

---

## Deploy CloudFormation stacks

Deploy in this order: VPC first, then the reaper Lambda.

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

Run these commands from the **repo root** (the directory that contains `desk-infra/`). Use **`--build-in-source`** so the reaper Makefile finds `../../desk-sdk`; a plain `sam build` from `desk-infra` would run in a scratch dir without desk-sdk. For first-time deploy you may need `--resolve-s3` (or `--guided`) so SAM can upload the artifact to S3.

First-time deploy (guided setup):

```bash
cd desk-infra
sam build --build-in-source --template desk-reaper.yaml
sam deploy --guided --template-file .aws-sam/build/template.yaml --stack-name reaper --capabilities CAPABILITY_IAM --resolve-s3
```

On subsequent deploys (after the guided config is saved to `samconfig.toml`):

```bash
cd desk-infra
sam build --build-in-source --template desk-reaper.yaml
sam deploy --template-file .aws-sam/build/template.yaml --stack-name reaper --capabilities CAPABILITY_IAM --resolve-s3
```

**Invoke and test:**

```bash
aws lambda invoke --cli-binary-format raw-in-base64-out --function-name desk-reaper --payload '{}' --region us-east-1 out.json && cat out.json
```

Response: `{"stopped": []}` when no workstations are overdue, or `{"stopped": [{"instance_id": "...", "name": "...", "shutdown_at": "..."}]}` when some were stopped.

**CloudWatch logs:** Log group `/aws/lambda/desk-reaper`. List recent streams and get events:

```bash
aws logs describe-log-streams --log-group-name /aws/lambda/desk-reaper --order-by LastEventTime --descending --max-items 1 --region us-east-1
aws logs get-log-events --log-group-name /aws/lambda/desk-reaper --log-stream-name "<stream-name>" --region us-east-1
```

---

## Lint CloudFormation

```bash
cfn-lint desk-infra/desk-vpc.yaml desk-infra/desk-reaper.yaml
```

Run from the repo root, or pass paths to the templates from your current directory.
