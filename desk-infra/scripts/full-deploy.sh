#!/usr/bin/env bash
# One-command deploy: SAM build + deploy (phase 1 and 2 for callback URL), then deploy.sh for frontend/sync.
# Usage: ./full-deploy.sh [stack-name]
# Set AWS_REGION=us-east-1 for WAF + CloudFront.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
STACK_NAME=${1:-desk-web}
CLOUDFORMATION_DIR="$INFRA_DIR/cloudformation"
# SAM and AWS CLI use AWS_DEFAULT_REGION when set
[ -n "$AWS_REGION" ] && export AWS_DEFAULT_REGION=$AWS_REGION

echo "==> Deploying stack $STACK_NAME (region: ${AWS_REGION:-${AWS_DEFAULT_REGION:-default}})"

# 1) SAM build
echo "==> SAM build..."
REPO_ROOT=$(cd "$INFRA_DIR/.." && pwd)
cd "$CLOUDFORMATION_DIR"
export DESK_SDK_PATH="$REPO_ROOT/desk-sdk"
sam build --template-file main.yaml

# 2) Deploy with placeholder callback (creates or updates stack)
echo "==> SAM deploy (phase 1)..."
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides "CognitoCallbackURL=https://placeholder.example.com" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# 3) Get CloudFront URL and redeploy with real callback
CF_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text 2>/dev/null || true)
if [ -z "$CF_URL" ] || [ "$CF_URL" == "None" ]; then
  echo "Warning: Could not get CloudFrontURL; Cognito callback may need updating later." >&2
else
  echo "==> SAM deploy (phase 2: CognitoCallbackURL=$CF_URL)..."
  sam deploy \
    --template-file .aws-sam/build/template.yaml \
    --stack-name "$STACK_NAME" \
    --parameter-overrides "CognitoCallbackURL=$CF_URL" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --resolve-s3 \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset 2>/dev/null || true
fi

# 4) Build frontend, sync S3, invalidate CloudFront (deploy.sh does sam build + deploy again, then sync)
echo "==> Running deploy.sh..."
"$SCRIPT_DIR/deploy.sh" "$STACK_NAME"

echo "==> Done. App URL: $CF_URL"
