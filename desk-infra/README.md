# desk-infra

CloudFormation templates for desk: app infrastructure and scheduled auto-stop reaper.

---

## Deploy CloudFormation stacks

Deploy in this order: app infrastructure first, then the reaper schedule stack.

### 1. App infrastructure

From the repo root:

```bash
aws cloudformation deploy \
  --stack-name desk \
  --template-file desk-infra/cloudformation/main.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```

The template provides:

- **HTTP API + Lambda** for desk API routes
- **Cognito/JWT auth** for user-facing API routes
- **AWS_IAM protected reap route** at `POST /api/workstations/reap`
- **S3/CloudFront/WAF** frontend infrastructure

`desk create` (CLI) uses the stack outputs automatically.

### 2. Reaper schedule (auto-stop)

The reaper schedule runs every 10 minutes and invokes `POST /api/workstations/reap` through API Gateway IAM auth. The API Lambda performs the reap logic.

After the `desk` stack deploys, get the API ID and deploy the schedule stack:

```bash
API_ID=$(aws cloudformation describe-stacks \
  --stack-name desk \
  --query "Stacks[0].Outputs[?OutputKey=='ApiId'].OutputValue" \
  --output text)

aws cloudformation deploy \
  --stack-name reaper \
  --template-file desk-infra/desk-reaper.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides HttpApiId="$API_ID" ApiStageName="\$default"
```

**CloudWatch logs:** inspect the desk API Lambda log group (`/aws/lambda/<stack-name>-api`).

```bash
STACK_NAME=desk
LOG_GROUP="/aws/lambda/${STACK_NAME}-api"
aws logs describe-log-streams --log-group-name "$LOG_GROUP" --order-by LastEventTime --descending --max-items 1 --region us-east-1
aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "<stream-name>" --region us-east-1
```

---

## Lint CloudFormation

```bash
cfn-lint desk-infra/cloudformation/main.yaml desk-infra/desk-reaper.yaml desk-infra/desk-vpc.yaml
```

Run from the repo root, or pass paths to the templates from your current directory.
