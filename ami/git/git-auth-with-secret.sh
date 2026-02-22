#!/usr/bin/env bash
#
# Fetch a GitHub App private key (or SSH key) from AWS Secrets Manager and
# configure the environment so git can authenticate with GitHub.
#
# Usage:
#   # Secret contains an SSH private key (e.g. deploy key):
#   GITHUB_KEY_SECRET_NAME=my-repo/deploy-key ./git-auth-with-secret.sh
#   # Then run git commands in the same shell, or:
#   GITHUB_KEY_SECRET_NAME=my-repo/deploy-key ./git-auth-with-secret.sh -- git clone git@github.com:org/repo.git
#
#   # Secret contains a GitHub App private key (PEM); get token and use for HTTPS git:
#   GITHUB_KEY_SECRET_NAME=my-app/private-key \
#   GITHUB_APP_ID=123456 \
#   GITHUB_INSTALLATION_ID=789 \
#   ./git-auth-with-secret.sh
#   # Or run a command with GH_TOKEN / git credential set:
#   ./git-auth-with-secret.sh -- git clone https://github.com/org/repo.git
#
# Environment:
#   GITHUB_KEY_SECRET_NAME  (required) Secrets Manager secret ID or name
#   GITHUB_APP_ID           (optional) GitHub App ID; if set with GITHUB_INSTALLATION_ID, secret is treated as App PEM and we fetch an installation token
#   GITHUB_INSTALLATION_ID  (optional) GitHub App installation ID
#   AWS_REGION              (optional) AWS region for Secrets Manager
#   AWS_PROFILE             (optional) AWS profile
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[git-auth]${NC} $1"; }
err()  { echo -e "${RED}[git-auth]${NC} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_FILE=""
CLEANUP_KEY_FILE=""

cleanup() {
  if [[ -n "$CLEANUP_KEY_FILE" && -f "$CLEANUP_KEY_FILE" ]]; then
    rm -f "$CLEANUP_KEY_FILE"
  fi
}
trap cleanup EXIT

usage() {
  echo "Usage: GITHUB_KEY_SECRET_NAME=<secret> [GITHUB_APP_ID=... GITHUB_INSTALLATION_ID=...] $0 [-- <command>]"
  echo "  If <command> is given, run it with git auth configured; otherwise start a subshell."
  exit 1
}

# Parse optional -- and command
CMD=()
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--" ]]; then
    shift
    CMD=("$@")
    break
  fi
  shift
done

SECRET_NAME="${GITHUB_KEY_SECRET_NAME:-}"
[[ -z "$SECRET_NAME" ]] && usage

AWS_ARGS=()
[[ -n "${AWS_REGION:-}" ]]  && AWS_ARGS+=(--region "$AWS_REGION")
[[ -n "${AWS_PROFILE:-}" ]] && AWS_ARGS+=(--profile "$AWS_PROFILE")

# Fetch secret value
info "Fetching secret: $SECRET_NAME"
SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_NAME" "${AWS_ARGS[@]}" --query SecretString --output text 2>/dev/null) || err "Failed to get secret (check AWS credentials and secret name)"

# Unwrap if JSON object (e.g. {"private_key":"-----BEGIN...", "app_id":"...", "installation_id":"..."} or {"key":"..."})
KEY_CONTENT=""
if echo "$SECRET_JSON" | head -c1 | grep -q '{'; then
  if command -v jq &>/dev/null; then
    KEY_CONTENT=$(echo "$SECRET_JSON" | jq -r '.private_key // .key // .')
    # If secret contains app_id/installation_id, use them when env vars are not set
    [[ -z "${GITHUB_APP_ID:-}" ]] && GITHUB_APP_ID=$(echo "$SECRET_JSON" | jq -r '.app_id // empty')
    [[ -z "${GITHUB_INSTALLATION_ID:-}" ]] && GITHUB_INSTALLATION_ID=$(echo "$SECRET_JSON" | jq -r '.installation_id // empty')
  else
    # Minimal extract: first value that looks like PEM or key
    KEY_CONTENT=$(echo "$SECRET_JSON" | sed -n 's/.*"private_key"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' | sed 's/\\n/\n/g')
    [[ -z "$KEY_CONTENT" ]] && KEY_CONTENT=$(echo "$SECRET_JSON" | sed -n 's/.*"key"[[:space:]]*:[[:space:]]*"\(.*\)".*/\1/p' | sed 's/\\n/\n/g')
  fi
fi
[[ -z "$KEY_CONTENT" ]] && KEY_CONTENT="$SECRET_JSON"

# Write to temp file with safe permissions
KEY_FILE=$(mktemp -t github_key.XXXXXX)
CLEANUP_KEY_FILE="$KEY_FILE"
printf '%s' "$KEY_CONTENT" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

# Detect GitHub App PEM vs SSH key
IS_PEM=0
grep -q "BEGIN RSA PRIVATE KEY\|BEGIN PRIVATE KEY" "$KEY_FILE" && IS_PEM=1

if [[ "$IS_PEM" -eq 1 && ( -z "${GITHUB_APP_ID:-}" || -z "${GITHUB_INSTALLATION_ID:-}" ) ]]; then
  err "Secret looks like a GitHub App private key (PEM). Set GITHUB_APP_ID and GITHUB_INSTALLATION_ID to get an installation token for git. Example:\n  GITHUB_APP_ID=123456 GITHUB_INSTALLATION_ID=789 $0"
fi

USE_APP_TOKEN=0
if [[ -n "${GITHUB_APP_ID:-}" && -n "${GITHUB_INSTALLATION_ID:-}" && "$IS_PEM" -eq 1 ]]; then
  USE_APP_TOKEN=1
fi

if [[ "$USE_APP_TOKEN" -eq 1 ]]; then
  # Get installation access token via npx (recommended) or fallback to JWT/curl
  info "Obtaining GitHub App installation token..."
  APP_ID="$GITHUB_APP_ID"
  INST_ID="$GITHUB_INSTALLATION_ID"

  if command -v npx &>/dev/null; then
    TOKEN=$(npx -y obtain-github-app-installation-access-token -a "$APP_ID" -i "$INST_ID" -k "$KEY_FILE" 2>/dev/null) || true
  else
    TOKEN=""
  fi

  if [[ -z "$TOKEN" ]]; then
    # Fallback: generate JWT and call GitHub API (no Node required)
    info "npx not available or failed; using built-in JWT..."
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
      "https://api.github.com/app/installations/${INST_ID}/access_tokens" \
      -d '{"permissions":{"contents":"read"}}' | grep -o '"token"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4)
  fi

  [[ -z "$TOKEN" ]] && err "Failed to get GitHub installation token (check App ID, Installation ID, PEM; install Node/npx for best results)"

  export GH_TOKEN="$TOKEN"
  # Askpass helper reads token from env so we don't write it to disk
  export GIT_ASKPASS="$SCRIPT_DIR/git-askpass-helper.sh"
  if [[ ! -x "$SCRIPT_DIR/git-askpass-helper.sh" ]]; then
    printf '%s\n' '#!/bin/sh' 'echo "${GH_TOKEN}"' > "$SCRIPT_DIR/git-askpass-helper.sh"
    chmod 700 "$SCRIPT_DIR/git-askpass-helper.sh"
  fi
  info "GitHub installation token set (GH_TOKEN). Use HTTPS URLs for git, e.g.: git clone https://github.com/org/repo.git"
  # Use credential helper so git pull/clone use the token for github.com
  git config --global credential.https://github.com.helper '!f() { echo "username=x-access-token"; echo "password=${GH_TOKEN}"; }; f'
else
  # SSH key: use for git over SSH (expand KEY_FILE now so it's baked in after exec $SHELL)
  export GIT_SSH_COMMAND="ssh -i \"$KEY_FILE\" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
  info "Git configured to use SSH key from Secrets Manager for git@github.com"
fi

if [[ ${#CMD[@]} -gt 0 ]]; then
  exec "${CMD[@]}"
else
  info "Starting shell with git auth configured. Exit when done."
  exec "$SHELL"
fi
