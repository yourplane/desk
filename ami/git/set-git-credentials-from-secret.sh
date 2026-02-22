#!/usr/bin/env bash
#
# Fetch GitHub App credentials (app id, installation id, private key) from an
# AWS Secrets Manager secret, obtain an installation access token, and set
# git's global credential helper so all git operations to GitHub use that token.
#
# The token is written to ~/.config/git-auth/github-token and credential.https://github.com.helper
# is set to this directory's git-credential-helper.sh. GitHub App tokens expire
# (typically after 1 hour); re-run this script to refresh, or use
# git-credential-refresh-daemon.sh for automatic refresh.
#
# Usage:
#   GITHUB_KEY_SECRET_NAME=my-github-app ./set-git-credentials-from-secret.sh
#
# Environment:
#   GITHUB_KEY_SECRET_NAME  (required) Secrets Manager secret ID or name.
#                           Secret must be JSON with app_id, installation_id, private_key.
#   GIT_AUTH_TOKEN_FILE     (optional) Where to write the token; default: ~/.config/git-auth/github-token
#   AWS_REGION              (optional) AWS region
#   AWS_PROFILE             (optional) AWS profile
#
# Requirements: aws CLI, jq, openssl, curl (or npx for token)
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[set-git-credentials]${NC} $1"; }
err()  { echo -e "${RED}[set-git-credentials]${NC} $1" >&2; exit 1; }

usage() {
  echo "Usage: GITHUB_KEY_SECRET_NAME=<secret> $0"
  echo "  Optional: GIT_AUTH_TOKEN_FILE, AWS_REGION, AWS_PROFILE"
  echo "  Secret must be JSON with app_id, installation_id, and private_key (e.g. from create-github-app-secret.sh)."
  exit 1
}

SECRET_NAME="${GITHUB_KEY_SECRET_NAME:-}"
[[ -z "$SECRET_NAME" ]] && usage

command -v jq &>/dev/null || err "jq is required"
command -v aws &>/dev/null || err "aws CLI is required"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${GIT_AUTH_TOKEN_FILE:-${HOME}/.config/git-auth/github-token}"
KEY_FILE=""
CLEANUP_KEY_FILE=""

cleanup() {
  if [[ -n "$CLEANUP_KEY_FILE" && -f "$CLEANUP_KEY_FILE" ]]; then
    rm -f "$CLEANUP_KEY_FILE"
  fi
}
trap cleanup EXIT

AWS_ARGS=()
[[ -n "${AWS_REGION:-}" ]]  && AWS_ARGS+=(--region "$AWS_REGION")
[[ -n "${AWS_PROFILE:-}" ]] && AWS_ARGS+=(--profile "$AWS_PROFILE")

# Fetch secret
info "Fetching secret: $SECRET_NAME"
SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" "${AWS_ARGS[@]}" --query SecretString --output text 2>/dev/null) || err "Failed to get secret (check AWS credentials and secret name)"

# Parse app_id, installation_id, private_key (required for this script)
APP_ID=$(echo "$SECRET_JSON" | jq -r '.app_id // empty')
INSTALLATION_ID=$(echo "$SECRET_JSON" | jq -r '.installation_id // empty')
KEY_CONTENT=$(echo "$SECRET_JSON" | jq -r '.private_key // .key // empty')

[[ -z "$APP_ID" ]] && err "Secret is missing app_id (use a secret created by create-github-app-secret.sh)"
[[ -z "$INSTALLATION_ID" ]] && err "Secret is missing installation_id"
[[ -z "$KEY_CONTENT" ]] && err "Secret is missing private_key"

# Write PEM to temp file
KEY_FILE=$(mktemp -t github_key.XXXXXX)
CLEANUP_KEY_FILE="$KEY_FILE"
printf '%s' "$KEY_CONTENT" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

# Obtain installation access token
info "Obtaining GitHub App installation token..."
TOKEN=""
if command -v npx &>/dev/null; then
  TOKEN=$(npx -y obtain-github-app-installation-access-token -a "$APP_ID" -i "$INSTALLATION_ID" -k "$KEY_FILE" 2>/dev/null) || true
fi

if [[ -z "$TOKEN" ]]; then
  info "Using built-in JWT fallback..."
  NOW=$(date +%s)
  EXP=$((NOW + 600))
  B64URL() { base64 | tr -d '\n' | tr '+/' '-_' | tr -d '='; }
  HEADER=$(echo -n '{"alg":"RS256","typ":"JWT"}' | B64URL)
  PAYLOAD=$(echo -n "{\"iat\":$NOW,\"exp\":$EXP,\"iss\":\"$APP_ID\"}" | B64URL)
  SIGN_INPUT="${HEADER}.${PAYLOAD}"
  SIGNATURE=$(echo -n "$SIGN_INPUT" | openssl dgst -sha256 -sign "$KEY_FILE" | B64URL)
  JWT="${SIGN_INPUT}.${SIGNATURE}"
  TOKEN=$(curl -sS -X POST \
    -H "Authorization: Bearer $JWT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/app/installations/${INSTALLATION_ID}/access_tokens" \
    -d '{"permissions":{"contents":"read"}}' | jq -r '.token // empty')
fi

[[ -z "$TOKEN" ]] && err "Failed to get GitHub installation token (check App ID, Installation ID, PEM; install Node/npx for best results)"

# Write token to persistent file
TOKEN_DIR="$(dirname "$TOKEN_FILE")"
mkdir -p "$TOKEN_DIR"
printf '%s' "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
info "Token written to $TOKEN_FILE"

# Set global credential helper so all git commands use this token for github.com
# Inline helper reads from the same path we wrote the token to (baked in so GIT_AUTH_TOKEN_FILE is respected)
CRED_HELPER="!f() { [ -r \"$TOKEN_FILE\" ] || exit 1; echo \"username=x-access-token\"; echo \"password=\$(cat \"$TOKEN_FILE\")\"; }; f"
git config --global credential.https://github.com.helper "$CRED_HELPER"
info "Git global credential helper set for https://github.com"

info "Done. Git will use the token for HTTPS operations to GitHub (e.g. clone, pull, push)."
info "Token expires in about 1 hour; re-run this script to refresh."
