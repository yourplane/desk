#!/usr/bin/env bash
# One-command deploy: same as deploy.sh (SAM build/deploy, frontend build, S3 sync, CloudFront invalidation).
# Usage: ./full-deploy.sh [stack-name]
# Set AWS_REGION=us-east-1 for WAF + CloudFront.
# Optional env: DESK_CUSTOM_DOMAIN_NAME, DESK_ACM_CERTIFICATE_ARN (ACM in us-east-1; required together).
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STACK_NAME=${1:-desk-web}
[ -n "$AWS_REGION" ] && export AWS_DEFAULT_REGION=$AWS_REGION
exec "$SCRIPT_DIR/deploy.sh" "$STACK_NAME"
