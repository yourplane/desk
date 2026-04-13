#!/usr/bin/env bash
# Build and deploy the desk web app stack using SAM for the Lambda.
# Usage: ./deploy.sh [stack-name] [aws-profile]
# Requires: stack already created (run full-deploy.sh or sam deploy first with CognitoCallbackURL).
# Also deploys the desk-router CloudFormation stack (latest self-owned router-ami-* AMI) when present.
# Requires VPC stack "desk" (exports for subnets). Builds frontend, runs sam build + sam deploy, syncs S3, invalidates CloudFront.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(cd "$INFRA_DIR/.." && pwd)
STACK_NAME=${1:-desk-web}
AWS_PROFILE=${2:-}
CLOUDFORMATION_DIR="$INFRA_DIR/cloudformation"
[ -n "$AWS_REGION" ] && export AWS_DEFAULT_REGION=$AWS_REGION
[ -n "$AWS_PROFILE" ] && export AWS_PROFILE
# Ensure SAM always has a region (falls back to configured default).
if [ -z "$AWS_DEFAULT_REGION" ]; then
  AWS_DEFAULT_REGION=$(aws configure get region 2>/dev/null || true)
  [ -n "$AWS_DEFAULT_REGION" ] && export AWS_DEFAULT_REGION
fi

echo "==> Stack: $STACK_NAME, Repo root: $REPO_ROOT"

# Deploy managed router ASG (desk-router.yaml). Skips if no router AMI exists in the account.
echo "==> Deploying desk-router stack..."
ROUTER_AMI=$(aws ec2 describe-images --owners self \
  --filters "Name=name,Values=router-ami-*" \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text 2>/dev/null || true)
if [ -z "$ROUTER_AMI" ] || [ "$ROUTER_AMI" = "None" ]; then
  echo "Warning: No self-owned router-ami-* AMI found; skipping desk-router deploy." >&2
else
  aws cloudformation deploy \
    --stack-name desk-router \
    --template-file "$INFRA_DIR/desk-router.yaml" \
    --parameter-overrides "RouterAmiId=${ROUTER_AMI}" \
    --capabilities CAPABILITY_NAMED_IAM
fi

# Build metadata for frontend (displayed in UI)
export VITE_BUILD_AT=$(date +"%b %d, %Y %H:%M %Z")
export VITE_BUILD_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || true)

# 0. Get stack outputs for frontend build (Cognito config)
_get() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }
if _get FrontendBucketName >/dev/null 2>&1; then
  export VITE_COGNITO_USER_POOL_ID=$(_get UserPoolId)
  export VITE_COGNITO_CLIENT_ID=$(_get AppClientId)
  export VITE_COGNITO_DOMAIN=$(_get UserPoolDomain)
  export VITE_COGNITO_REDIRECT_URI=$(_get CloudFrontURL)
  export VITE_COGNITO_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null)}
fi

# 1. Build frontend
echo "==> Building frontend..."
cd "$REPO_ROOT/desk-frontend"
npm install
npm run build

# 2. SAM build and deploy (SAM uses its own deployment bucket; no custom artifacts bucket)
echo "==> SAM build..."
cd "$CLOUDFORMATION_DIR"
export DESK_SDK_PATH="$REPO_ROOT/desk-sdk"
sam build --template-file main.yaml
CF_URL=$(_get CloudFrontURL)
[ -z "$CF_URL" ] || [ "$CF_URL" = "None" ] && CF_URL=""
CALLBACK_URL="${CF_URL:-https://placeholder.example.com}"
echo "==> SAM deploy (CognitoCallbackURL=$CALLBACK_URL)..."
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides "CognitoCallbackURL=$CALLBACK_URL" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# 3. Sync frontend to S3
FRONTEND_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text 2>/dev/null || true)
[ -z "$FRONTEND_BUCKET" ] || [ "$FRONTEND_BUCKET" = "None" ] && { echo "Error: Stack has no FrontendBucketName output. Ensure the stack is fully created." >&2; exit 1; }
echo "==> Syncing frontend to s3://$FRONTEND_BUCKET..."
aws s3 sync "$REPO_ROOT/desk-frontend/dist" "s3://$FRONTEND_BUCKET" --delete

# 4. Invalidate CloudFront
DIST_ID=$(_get CloudFrontDistributionId)
if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
  echo "==> Invalidating CloudFront distribution $DIST_ID..."
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
else
  echo "Warning: Could not determine CloudFront distribution ID; skip invalidation."
fi

echo "==> Done. App URL: $(_get CloudFrontURL 2>/dev/null || echo 'see stack outputs')"
