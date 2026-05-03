#!/usr/bin/env bash
# Build and deploy the desk web app stack using SAM for the Lambda.
# Usage: ./deploy.sh [stack-name] [aws-profile]
# Optional env: DESK_CUSTOM_DOMAIN_NAME, DESK_ACM_CERTIFICATE_ARN (both required together; ACM must be in us-east-1).
# Optional: DESK_ROUTE53_HOSTED_ZONE_ID (Z... for public zone = CustomDomainName), or DESK_ROUTE53_AUTO_LOOKUP=true.
# ACM for the app should include SANs for the apex and *.apex (e.g. desk.example.com and *.desk.example.com) for public web routes.
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
# CloudFront → origin (ALB) prefix list; name is region/account-specific but this is the standard global name.
CLOUDFRONT_VPC_PL=$(aws ec2 describe-managed-prefix-lists \
  --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
  --query 'PrefixLists[0].PrefixListId' --output text 2>/dev/null || true)
if [ -z "$ROUTER_AMI" ] || [ "$ROUTER_AMI" = "None" ]; then
  echo "Warning: No self-owned router-ami-* AMI found; skipping desk-router deploy." >&2
elif [ -z "$CLOUDFRONT_VPC_PL" ] || [ "$CLOUDFRONT_VPC_PL" = "None" ]; then
  echo "Error: Could not resolve EC2 managed prefix list com.amazonaws.global.cloudfront.origin-facing (needed for desk-router ALB)." >&2
  exit 1
else
  aws cloudformation deploy \
    --stack-name desk-router \
    --template-file "$INFRA_DIR/desk-router.yaml" \
    --parameter-overrides \
      "RouterAmiId=${ROUTER_AMI}" \
      "CloudFrontVpcOriginPrefixListId=${CLOUDFRONT_VPC_PL}" \
      "WebRouterBaseDomain=${DESK_CUSTOM_DOMAIN_NAME:-}" \
    --capabilities CAPABILITY_NAMED_IAM
fi

# Build metadata for frontend (displayed in UI)
export VITE_BUILD_AT=$(date +"%b %d, %Y %H:%M %Z")
export VITE_BUILD_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || true)
# VITE_WEB_ROUTER_HOST_SUFFIX / cookie domain: set after SAM deploy from env or stack (see below).

# 1. SAM build and deploy (Cognito callback URLs are derived in the template from CloudFront + optional custom domain)
echo "==> SAM build..."
cd "$CLOUDFORMATION_DIR"
export DESK_SDK_PATH="$REPO_ROOT/desk-sdk"
sam build --template-file main.yaml
echo "==> SAM deploy (CustomDomainName=${DESK_CUSTOM_DOMAIN_NAME:-<empty>})..."
SAM_PARAM_ARGS=()
if [ -n "${DESK_CUSTOM_DOMAIN_NAME:-}" ] || [ -n "${DESK_ACM_CERTIFICATE_ARN:-}" ]; then
  if [ -z "${DESK_CUSTOM_DOMAIN_NAME:-}" ] || [ -z "${DESK_ACM_CERTIFICATE_ARN:-}" ]; then
    echo "Error: set both DESK_CUSTOM_DOMAIN_NAME and DESK_ACM_CERTIFICATE_ARN (ACM in us-east-1), or neither." >&2
    exit 1
  fi
  SAM_PARAM_ARGS=(
    --parameter-overrides
    "CustomDomainName=${DESK_CUSTOM_DOMAIN_NAME}"
    "AcmCertificateArn=${DESK_ACM_CERTIFICATE_ARN}"
    "EnableWebRouterCloudFront=${DESK_ENABLE_WEB_ROUTER_CLOUDFRONT:-false}"
  )
  # Optional: resolve Route 53 hosted zone for CustomDomainName (public zone matching the apex FQDN)
  if [ "${DESK_ROUTE53_AUTO_LOOKUP:-false}" = "true" ] && [ -z "${DESK_ROUTE53_HOSTED_ZONE_ID:-}" ]; then
    _zone=$(aws route53 list-hosted-zones-by-name --dns-name "${DESK_CUSTOM_DOMAIN_NAME}." \
      --query 'HostedZones[0].Id' --output text 2>/dev/null || true)
    if [ -n "$_zone" ] && [ "$_zone" != "None" ]; then
      export DESK_ROUTE53_HOSTED_ZONE_ID="${_zone##*/hostedzone/}"
    fi
  fi
  if [ -n "${DESK_ROUTE53_HOSTED_ZONE_ID:-}" ]; then
    SAM_PARAM_ARGS+=( "Route53HostedZoneId=${DESK_ROUTE53_HOSTED_ZONE_ID}" )
  fi
fi
sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  "${SAM_PARAM_ARGS[@]}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# 2. Stack outputs for frontend Cognito config (after deploy so values match the template)
_get() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }
_stack_param() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue | [0]" --output text 2>/dev/null || true
}
if _get FrontendBucketName >/dev/null 2>&1; then
  export VITE_COGNITO_USER_POOL_ID=$(_get UserPoolId)
  export VITE_COGNITO_CLIENT_ID=$(_get AppClientId)
  export VITE_COGNITO_DOMAIN=$(_get UserPoolDomain)
  export VITE_COGNITO_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null)}
fi

# Public web routes + cookie Domain: use shell env if set, else read CustomDomainName from the stack
# so `npm run build` still embeds VITE_WEB_ROUTER_HOST_SUFFIX when redeploying without DESK_CUSTOM_DOMAIN_NAME.
DESK_APEX_FOR_VITE="${DESK_CUSTOM_DOMAIN_NAME:-}"
if [ -z "$DESK_APEX_FOR_VITE" ] || [ "$DESK_APEX_FOR_VITE" = "None" ]; then
  DESK_APEX_FOR_VITE=$(_stack_param CustomDomainName)
  [ "$DESK_APEX_FOR_VITE" = "None" ] && DESK_APEX_FOR_VITE=
fi
if [ -n "$DESK_APEX_FOR_VITE" ]; then
  export VITE_COOKIE_DOMAIN=".${DESK_APEX_FOR_VITE}"
  export VITE_WEB_ROUTER_HOST_SUFFIX="${DESK_APEX_FOR_VITE}"
  echo "==> Frontend web-route host suffix: ${VITE_WEB_ROUTER_HOST_SUFFIX}"
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
