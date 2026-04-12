#!/usr/bin/env bash
#
# Create or update an AWS Secrets Manager secret containing GitHub App
# credentials: app id, installation id, and private key (PEM). All three
# are stored in a single secret; reference that secret from ~/.config/git-auth/bots.json
# (see set-git-credentials-from-secret.sh) alongside the GitHub org for routing.
#
# Usage:
#   ./create-github-app-secret.sh --secret-name my-github-app \\
#     --app-id 123456 --installation-id 789012 \\
#     --private-key-file /path/to/private-key.pem
#
# Options:
#   --secret-name NAME       Name or ID of the secret (required)
#   --app-id ID              GitHub App ID (required)
#   --installation-id ID     GitHub App installation ID (required)
#   --private-key-file PATH  Path to the PEM file (required)
#   --region REGION          AWS region (optional)
#   --profile PROFILE        AWS profile (optional)
#
# Requirements: aws CLI, python3 (for JSON encoding)
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[create-github-app-secret]${NC} $1"; }
err()  { echo -e "${RED}[create-github-app-secret]${NC} $1" >&2; exit 1; }

SECRET_NAME=""
APP_ID=""
INSTALLATION_ID=""
PRIVATE_KEY_FILE=""
AWS_REGION=""
AWS_PROFILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --secret-name)         SECRET_NAME="$2"; shift 2 ;;
    --app-id)              APP_ID="$2"; shift 2 ;;
    --installation-id)     INSTALLATION_ID="$2"; shift 2 ;;
    --private-key-file)    PRIVATE_KEY_FILE="$2"; shift 2 ;;
    --region)              AWS_REGION="$2"; shift 2 ;;
    --profile)             AWS_PROFILE="$2"; shift 2 ;;
    -h|--help)             usage; exit 0 ;;
    *)                     err "Unknown option: $1"; exit 1 ;;
  esac
done

usage() {
  echo "Usage: $0 --secret-name NAME --app-id ID --installation-id ID --private-key-file PATH"
  echo "  Optional: --region REGION, --profile PROFILE"
  echo ""
  echo "Creates or updates an AWS Secrets Manager secret with keys:"
  echo "  app_id, installation_id, private_key"
  exit 1
}

[[ -n "$SECRET_NAME" ]]       || err "Missing --secret-name"
[[ -n "$APP_ID" ]]            || err "Missing --app-id"
[[ -n "$INSTALLATION_ID" ]]   || err "Missing --installation-id"
[[ -n "$PRIVATE_KEY_FILE" ]]  || err "Missing --private-key-file"
[[ -f "$PRIVATE_KEY_FILE" ]]  || err "Private key file not found: $PRIVATE_KEY_FILE"

AWS_ARGS=()
[[ -n "$AWS_REGION" ]]  && AWS_ARGS+=(--region "$AWS_REGION")
[[ -n "$AWS_PROFILE" ]] && AWS_ARGS+=(--profile "$AWS_PROFILE")

# Build JSON: app_id, installation_id, private_key (PEM with newlines preserved and escaped)
SECRET_JSON=$(python3 -c "
import json, sys
with open(sys.argv[1], 'r') as f:
    pk = f.read()
print(json.dumps({
    'app_id': sys.argv[2],
    'installation_id': sys.argv[3],
    'private_key': pk
}))
" "$PRIVATE_KEY_FILE" "$APP_ID" "$INSTALLATION_ID") || err "Failed to build secret JSON"

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" "${AWS_ARGS[@]}" &>/dev/null; then
  info "Updating existing secret: $SECRET_NAME"
  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" \
    "${AWS_ARGS[@]}" || err "Failed to update secret"
  info "Secret updated successfully."
else
  info "Creating new secret: $SECRET_NAME"
  aws secretsmanager create-secret \
    --name "$SECRET_NAME" \
    --secret-string "$SECRET_JSON" \
    "${AWS_ARGS[@]}" || err "Failed to create secret"
  info "Secret created successfully."
fi

info "Add to bots.json: { \"secret\": \"$SECRET_NAME\", \"org\": \"<GitHubOrg>\" } then run set-git-credentials-from-secret.sh"
