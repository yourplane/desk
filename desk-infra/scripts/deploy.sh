#!/usr/bin/env bash
# Build and deploy the desk web app stack using SAM for the Lambda.
# Usage: ./deploy.sh [stack-name] [aws-profile]
# Optional env: DESK_CUSTOM_DOMAIN_NAME, DESK_ACM_CERTIFICATE_ARN (both required together; ACM must be in us-east-1).
# Also deploys the desk-router CloudFormation stack (latest self-owned router-ami-* AMI) when present.
# Requires VPC stack "desk" (exports for subnets). Builds frontend after stack deploy, syncs S3, invalidates CloudFront.
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

# 1. SAM build and deploy (Cognito callback URLs are derived in the template from CloudFront + optional custom domain)
echo "==> SAM build..."
cd "$CLOUDFORMATION_DIR"
export DESK_SDK_PATH="$REPO_ROOT/desk-sdk"
sam build --template-file main.yaml
echo "==> SAM deploy (CustomDomainName=${DESK_CUSTOM_DOMAIN_NAME:-<empty>})..."
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides "CustomDomainName=${DESK_CUSTOM_DOMAIN_NAME:-} AcmCertificateArn=${DESK_ACM_CERTIFICATE_ARN:-}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# 2. Stack outputs for frontend Cognito config (after deploy so values match the template)
_get() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }
if _get FrontendBucketName >/dev/null 2>&1; then
  export VITE_COGNITO_USER_POOL_ID=$(_get UserPoolId)
  export VITE_COGNITO_CLIENT_ID=$(_get AppClientId)
  export VITE_COGNITO_DOMAIN=$(_get UserPoolDomain)
  export VITE_COGNITO_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null)}
fi

# 3. Build frontend
echo "==> Building frontend..."
cd "$REPO_ROOT/desk-frontend"
npm install
npm run build

# 4. Sync frontend to S3
FRONTEND_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text 2>/dev/null || true)
[ -z "$FRONTEND_BUCKET" ] || [ "$FRONTEND_BUCKET" = "None" ] && { echo "Error: Stack has no FrontendBucketName output. Ensure the stack is fully created." >&2; exit 1; }
echo "==> Syncing frontend to s3://$FRONTEND_BUCKET..."
aws s3 sync "$REPO_ROOT/desk-frontend/dist" "s3://$FRONTEND_BUCKET" --delete

# 5. Invalidate CloudFront
DIST_ID=$(_get CloudFrontDistributionId)
if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
  echo "==> Invalidating CloudFront distribution $DIST_ID..."
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
else
  echo "Warning: Could not determine CloudFront distribution ID; skip invalidation."
fi

echo "==> Done. Canonical URL: $(_get CanonicalAppURL 2>/dev/null || echo 'see stack outputs') (CloudFront: $(_get CloudFrontURL 2>/dev/null || echo '?'))"
