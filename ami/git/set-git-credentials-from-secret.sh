#!/usr/bin/env bash
#
# Read ~/.config/git-auth/bots.json (or GITHUB_KEY_SECRET_CONFIG), fetch each
# bot's AWS Secrets Manager secret, mint GitHub App installation tokens, write
# one file per org under ~/.config/git-auth/tokens/, and install the dispatcher
# credential helper for https://github.com.
#
# Usage:
#   ./set-git-credentials-from-secret.sh
#
# Environment:
#   GITHUB_KEY_SECRET_CONFIG  Path to bots JSON (default: ~/.config/git-auth/bots.json)
#   AWS_REGION, AWS_PROFILE   Optional; passed to aws CLI
#
# bots.json format:
#   { "bots": [ { "secret": "aws-secret-name", "org": "GitHubOrg" }, ... ] }
#
# Requirements: aws CLI, jq, openssl, curl (or npx for token minting)
#
set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}[set-git-credentials]${NC} $1"; }
warn() { echo -e "${YELLOW}[set-git-credentials]${NC} $1" >&2; }
err()  { echo -e "${RED}[set-git-credentials]${NC} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=git-auth-common.sh
source "$SCRIPT_DIR/git-auth-common.sh"

DISPATCHER="$SCRIPT_DIR/git-credential-github-dispatch.sh"
CONFIG="$(git_auth_default_config_path)"
if [[ "$CONFIG" != /* ]]; then
  CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
fi

[[ -f "$CONFIG" ]] || err "Missing config: $CONFIG (set GITHUB_KEY_SECRET_CONFIG or create ${GIT_AUTH_DIR}/bots.json)"
command -v jq &>/dev/null || err "jq is required"
command -v aws &>/dev/null || err "aws CLI is required"
[[ -x "$DISPATCHER" ]] || err "Dispatcher not executable: $DISPATCHER"

BOT_COUNT="$(jq '.bots | length' "$CONFIG")"
[[ "$BOT_COUNT" =~ ^[0-9]+$ && "$BOT_COUNT" -gt 0 ]] || err "Config must contain a non-empty \"bots\" array"

AWS_ARGS=()
[[ -n "${AWS_REGION:-}" ]]  && AWS_ARGS+=(--region "$AWS_REGION")
[[ -n "${AWS_PROFILE:-}" ]] && AWS_ARGS+=(--profile "$AWS_PROFILE")

# --- obtain installation token for one secret; key PEM is in key_file ---
mint_installation_token() {
  local secret_json="$1" key_file="$2"
  local app_id installation_id token

  app_id=$(echo "$secret_json" | jq -r '.app_id // empty')
  installation_id=$(echo "$secret_json" | jq -r '.installation_id // empty')
  [[ -n "$app_id" && -n "$installation_id" && -s "$key_file" ]] || return 1

  token=""
  if command -v npx &>/dev/null; then
    token=$(npx -y obtain-github-app-installation-access-token -a "$app_id" -i "$installation_id" -k "$key_file" 2>/dev/null) || true
  fi

  if [[ -z "$token" ]]; then
    local NOW EXP HEADER PAYLOAD SIGN_INPUT SIGNATURE JWT
    NOW=$(date +%s)
    EXP=$((NOW + 600))
    B64URL() { base64 | tr -d '\n' | tr '+/' '-_' | tr -d '='; }
    HEADER=$(echo -n '{"alg":"RS256","typ":"JWT"}' | B64URL)
    PAYLOAD=$(echo -n "{\"iat\":$NOW,\"exp\":$EXP,\"iss\":\"$app_id\"}" | B64URL)
    SIGN_INPUT="${HEADER}.${PAYLOAD}"
    SIGNATURE=$(echo -n "$SIGN_INPUT" | openssl dgst -sha256 -sign "$key_file" | B64URL)
    JWT="${SIGN_INPUT}.${SIGNATURE}"
    token=$(curl -sS -X POST \
      -H "Authorization: Bearer $JWT" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/app/installations/${installation_id}/access_tokens" \
      -d '{"permissions":{"contents":"write"}}' | jq -r '.token // empty')
  fi

  [[ -n "$token" ]] || return 1
  printf '%s' "$token"
}

# Refresh one bot: returns 0 on success
refresh_one_bot() {
  local secret_name="$1" org="$2"
  local secret_json key_content key_file token token_path

  info "Fetching secret: $secret_name (org: $org)"
  secret_json=$(aws secretsmanager get-secret-value --secret-id "$secret_name" "${AWS_ARGS[@]}" --query SecretString --output text 2>/dev/null) || {
    warn "Failed to get secret: $secret_name"
    return 1
  }

  key_content=$(echo "$secret_json" | jq -r '.private_key // .key // empty')
  [[ -n "$key_content" ]] || {
    warn "Secret $secret_name is missing private_key"
    return 1
  }

  key_file=$(mktemp -t github_key.XXXXXX)
  chmod 600 "$key_file"
  printf '%s' "$key_content" > "$key_file"

  token=""
  if ! token=$(mint_installation_token "$secret_json" "$key_file"); then
    rm -f "$key_file"
    warn "Failed to mint token for secret $secret_name (org $org)"
    return 1
  fi
  rm -f "$key_file"

  token_path="$(git_auth_token_file_for_org "$org")"
  mkdir -p "$(dirname "$token_path")"
  printf '%s' "$token" > "$token_path"
  chmod 600 "$token_path"
  info "Token written for org $org -> $token_path"
  return 0
}

mkdir -p "$GIT_AUTH_TOKENS_DIR"
chmod 700 "$GIT_AUTH_DIR" 2>/dev/null || true
chmod 700 "$GIT_AUTH_TOKENS_DIR" 2>/dev/null || true

success_count=0
for ((i = 0; i < BOT_COUNT; i++)); do
  secret_name="$(jq -r ".bots[$i].secret // empty" "$CONFIG")"
  org="$(jq -r ".bots[$i].org // empty" "$CONFIG")"
  [[ -n "$secret_name" && -n "$org" ]] || {
    warn "Skipping entry $i: missing secret or org"
    continue
  }
  if refresh_one_bot "$secret_name" "$org"; then
    success_count=$((success_count + 1))
  fi
done

[[ "$success_count" -gt 0 ]] || err "No bot credentials refreshed successfully"

mkdir -p "$GIT_AUTH_DIR"
chmod 700 "$GIT_AUTH_DIR"
printf '%s\n' "$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")" > "${GIT_AUTH_DIR}/config.path"
chmod 600 "${GIT_AUTH_DIR}/config.path"

CRED_HELPER="!\"$DISPATCHER\""
git config --global credential.https://github.com.helper "$CRED_HELPER"
git config --global credential.useHttpPath true
info "Git credential helper set to dispatcher for https://github.com"

info "Done ($success_count/$BOT_COUNT bot(s) refreshed)."
info "Tokens expire in about 1 hour; re-run or use git-credential-refresh-daemon.sh."
