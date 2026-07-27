#!/usr/bin/env bash
# Unauthenticated post-deploy smoke checks for desk-web.
# Usage: ./smoke-test.sh <expected-short-sha> [stack-name]
set -euo pipefail

EXPECTED_SHA="${1:?Usage: $0 <expected-short-sha> [stack-name]}"
STACK_NAME="${2:-desk-web}"

_get() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

echo "==> Smoke test for stack $STACK_NAME (expected SHA: $EXPECTED_SHA)"

STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].StackStatus' --output text)
echo "Stack status: $STATUS"
if [[ "$STATUS" != "UPDATE_COMPLETE" && "$STATUS" != "CREATE_COMPLETE" ]]; then
  echo "FAIL: stack not in a healthy state" >&2
  exit 1
fi

CANONICAL=$(_get CanonicalAppURL)
COGNITO_DOMAIN=$(_get UserPoolDomain)
REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}}

if [[ -z "$CANONICAL" || "$CANONICAL" == "None" ]]; then
  echo "FAIL: no CanonicalAppURL output" >&2
  exit 1
fi

echo "==> Canonical URL: $CANONICAL"
HTTP_CODE=$(curl -sS -o /tmp/desk-smoke-index.html -w '%{http_code}' "$CANONICAL/" --max-time 30)
echo "Canonical HTTP status: $HTTP_CODE"
if [[ "$HTTP_CODE" != "200" ]]; then
  echo "FAIL: canonical URL did not return 200" >&2
  exit 1
fi

if [[ -z "$COGNITO_DOMAIN" || "$COGNITO_DOMAIN" == "None" ]]; then
  echo "FAIL: no UserPoolDomain output" >&2
  exit 1
fi

COGNITO_LOGIN="https://${COGNITO_DOMAIN}.auth.${REGION}.amazoncognito.com/login"
echo "==> Cognito hosted UI: $COGNITO_LOGIN"
COGNITO_CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$COGNITO_LOGIN" --max-time 30)
echo "Cognito HTTP status: $COGNITO_CODE"
if [[ "$COGNITO_CODE" != "200" && "$COGNITO_CODE" != "302" ]]; then
  echo "FAIL: Cognito hosted UI not reachable" >&2
  exit 1
fi

echo "==> Checking deployed build SHA in frontend assets..."
JS_PATH=$(grep -oE '/assets/main-[A-Za-z0-9_-]+\.js' /tmp/desk-smoke-index.html | head -1 || true)
if [[ -z "$JS_PATH" ]]; then
  echo "FAIL: could not find main JS bundle in index.html" >&2
  exit 1
fi

curl -sS "${CANONICAL%/}${JS_PATH}" -o /tmp/desk-smoke-main.js --max-time 60
if grep -q "$EXPECTED_SHA" /tmp/desk-smoke-main.js; then
  echo "PASS: deployed bundle contains expected SHA ($EXPECTED_SHA)"
else
  echo "FAIL: deployed bundle does not contain expected SHA ($EXPECTED_SHA)" >&2
  echo "Hint: CloudFront may still be propagating; retry in a few minutes." >&2
  exit 1
fi

echo "==> All smoke checks passed."
