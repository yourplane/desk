#!/usr/bin/env bash
# Build and deploy the desk web app stack.
# Usage: ./deploy.sh [stack-name] [aws-profile]
# Requires: stack already created (aws cloudformation deploy) with params AllowedEmail, GoogleClientId, GoogleClientSecret, CognitoCallbackURL.
# This script: 1) Builds frontend, 2) Packages and uploads Lambda code, 3) Syncs frontend to S3, 4) Invalidates CloudFront.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INFRA_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(cd "$INFRA_DIR/.." && pwd)
STACK_NAME=${1:-desk-web}
AWS_PROFILE=${2:-}

export AWS_PROFILE

echo "==> Stack: $STACK_NAME, Repo root: $REPO_ROOT"

# 0. Get stack outputs for frontend build (Cognito config)
_get() { aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true; }
if _get ArtifactsBucketName >/dev/null 2>&1; then
  export VITE_COGNITO_USER_POOL_ID=$(_get UserPoolId)
  export VITE_COGNITO_CLIENT_ID=$(_get AppClientId)
  export VITE_COGNITO_DOMAIN=$(_get UserPoolDomain)
  export VITE_COGNITO_REDIRECT_URI=$(_get CloudFrontURL)
  export VITE_COGNITO_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null)}
fi

# 1. Build frontend
echo "==> Building frontend..."
cd "$REPO_ROOT/desk-frontend"
npm ci --omit=optional 2>/dev/null || npm install
npm run build

# 2. Package authorizer Lambda
echo "==> Packaging authorizer Lambda..."
AUTH_ZIP=$(mktemp -u).zip
cd "$INFRA_DIR/auth"
pip install -q -r requirements.txt -t .package
cp handler.py .package/
cd .package && zip -qr "$AUTH_ZIP" . && cd ..
mv .package/handler.py .
rm -rf .package
AUTH_KEY="webapp/authorizer-$(date +%Y%m%d%H%M%S).zip"

# 3. Package desk-api Lambda (desk-api + desk-sdk + deps)
echo "==> Packaging desk-api Lambda..."
API_BUILD_DIR=$(mktemp -d)
trap "rm -rf '$API_BUILD_DIR'" EXIT
cp -r "$REPO_ROOT/desk-api/app" "$API_BUILD_DIR/"
cp "$REPO_ROOT/desk-api/lambda_handler.py" "$API_BUILD_DIR/"
mkdir -p "$API_BUILD_DIR/desk"
cp -r "$REPO_ROOT/desk-sdk/src/desk/"* "$API_BUILD_DIR/desk/"
pip install -q --target "$API_BUILD_DIR" fastapi "mangum>=0.17" "boto3>=1.34" 2>/dev/null || true
cd "$API_BUILD_DIR"
API_ZIP=$(mktemp -u).zip
zip -qr "$API_ZIP" .
API_KEY="webapp/desk-api-$(date +%Y%m%d%H%M%S).zip"

# 4. Get artifacts bucket from stack
echo "==> Getting stack outputs..."
BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='ArtifactsBucketName'].OutputValue" --output text 2>/dev/null || true)
if [ -z "$BUCKET" ]; then
  echo "Error: Stack $STACK_NAME not found or has no ArtifactsBucketName output. Deploy the CloudFormation stack first." >&2
  exit 1
fi

# 5. Upload Lambda zips
echo "==> Uploading Lambda packages to s3://$BUCKET/..."
aws s3 cp "$AUTH_ZIP" "s3://$BUCKET/$AUTH_KEY"
aws s3 cp "$API_ZIP" "s3://$BUCKET/$API_KEY"
rm -f "$AUTH_ZIP" "$API_ZIP"

# 6. Update Lambda function codes
AUTHORIZER_FN="${STACK_NAME}-authorizer"
API_FN="${STACK_NAME}-api"
echo "==> Updating Lambda $AUTHORIZER_FN..."
aws lambda update-function-code --function-name "$AUTHORIZER_FN" --s3-bucket "$BUCKET" --s3-key "$AUTH_KEY" --no-clobber 2>/dev/null || true
echo "==> Updating Lambda $API_FN..."
aws lambda update-function-code --function-name "$API_FN" --s3-bucket "$BUCKET" --s3-key "$API_KEY" --no-clobber 2>/dev/null || true

# 7. Get frontend bucket and sync
FRONTEND_BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" --output text)
echo "==> Syncing frontend to s3://$FRONTEND_BUCKET..."
aws s3 sync "$REPO_ROOT/desk-frontend/dist" "s3://$FRONTEND_BUCKET" --delete

# 8. Invalidate CloudFront
DIST_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" --output text 2>/dev/null || true)
if [ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ]; then
  echo "==> Invalidating CloudFront distribution $DIST_ID..."
  aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
else
  echo "Warning: Could not determine CloudFront distribution ID; skip invalidation."
fi

echo "==> Done. App URL: $(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query "Stacks[0].Outputs[?OutputKey=='CloudFrontURL'].OutputValue" --output text 2>/dev/null || echo 'see stack outputs')"
