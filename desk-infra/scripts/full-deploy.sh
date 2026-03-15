#!/usr/bin/env bash
# One-command deploy: create/update CloudFormation stack (with Cognito callback), then build and sync.
# Usage: ALLOWED_EMAIL=you@example.com GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... ./full-deploy.sh [stack-name]
# Region: set AWS_REGION=us-east-1 (required for WAF + CloudFront).
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
STACK_NAME=${1:-desk-web}
TEMPLATE="$INFRA_DIR/cloudformation/main.yaml"

if [ -z "$ALLOWED_EMAIL" ] || [ -z "$GOOGLE_CLIENT_ID" ] || [ -z "$GOOGLE_CLIENT_SECRET" ]; then
  echo "Error: Set ALLOWED_EMAIL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET (env or export)." >&2
  echo "Example: ALLOWED_EMAIL=you@example.com GOOGLE_CLIENT_ID=xxx GOOGLE_CLIENT_SECRET=yyy ./full-deploy.sh" >&2
  exit 1
fi

echo "==> Deploying stack $STACK_NAME (region: ${AWS_REGION:-default})"

# 1) Deploy with placeholder callback (or existing params)
echo "==> CloudFormation deploy (phase 1)..."
aws cloudformation deploy \
  --template-file "$TEMPLATE" \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
    "AllowedEmail=$ALLOWED_EMAIL" \
    "GoogleClientId=$GOOGLE_CLIENT_ID" \
    "GoogleClientSecret=$GOOGLE_CLIENT_SECRET" \
    "CognitoCallbackURL=https://placeholder.example.com" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset 2>/dev/null || true

# 2) Get CloudFront URL and redeploy with real callback
CF_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text 2>/dev/null || true)
if [ -z "$CF_URL" ] || [ "$CF_URL" == "None" ]; then
  echo "Warning: Could not get CloudFrontURL; Cognito callback may need updating later." >&2
else
  echo "==> CloudFormation deploy (phase 2: CognitoCallbackURL=$CF_URL)..."
  aws cloudformation deploy \
    --template-file "$TEMPLATE" \
    --stack-name "$STACK_NAME" \
    --parameter-overrides \
      "AllowedEmail=$ALLOWED_EMAIL" \
      "GoogleClientId=$GOOGLE_CLIENT_ID" \
      "GoogleClientSecret=$GOOGLE_CLIENT_SECRET" \
      "CognitoCallbackURL=$CF_URL" \
    --capabilities CAPABILITY_IAM \
    --no-fail-on-empty-changeset 2>/dev/null || true
fi

# 3) Build frontend, upload Lambdas, sync S3, invalidate CloudFront
echo "==> Running deploy.sh..."
"$SCRIPT_DIR/deploy.sh" "$STACK_NAME"

echo "==> Done. App URL: $CF_URL"
