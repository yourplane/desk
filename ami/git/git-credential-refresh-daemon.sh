#!/usr/bin/env bash
#
# Run continuously to keep GitHub (HTTPS) git credentials refreshed. Fetches a
# new GitHub App installation token on startup and then every N minutes, writes
# it to a file, and configures git to use it via a credential helper. Intended
# for GitHub App (PEM) mode only; SSH deploy keys do not expire.
#
# Configure to run on startup via systemd (see git-credential-refresh.service)
# or cron @reboot, or run manually in a terminal/screen.
#
# Environment (same as git-auth-with-secret.sh for App mode):
#   GITHUB_KEY_SECRET_NAME  (required) Secrets Manager secret ID or name
#   GITHUB_APP_ID           (required) GitHub App ID
#   GITHUB_INSTALLATION_ID  (required) GitHub App installation ID
#   GIT_AUTH_TOKEN_FILE    (optional) Path to write the token; default: ~/.config/git-auth/github-token
#   GIT_AUTH_REFRESH_INTERVAL_SECONDS  (optional) Seconds between refreshes; default: 1800 (30 min)
#   AWS_REGION / AWS_PROFILE  (optional) As in git-auth-with-secret.sh
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[git-credential-refresh]${NC} $1"; }
err()  { echo -e "${RED}[git-credential-refresh]${NC} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${GIT_AUTH_TOKEN_FILE:-${HOME}/.config/git-auth/github-token}"
REFRESH_INTERVAL="${GIT_AUTH_REFRESH_INTERVAL_SECONDS:-1800}"
CREDENTIAL_HELPER_SCRIPT="$SCRIPT_DIR/git-credential-helper.sh"

usage() {
  echo "Usage: GITHUB_KEY_SECRET_NAME=<secret> GITHUB_APP_ID=<id> GITHUB_INSTALLATION_ID=<id> $0"
  echo "  Optional: GIT_AUTH_TOKEN_FILE, GIT_AUTH_REFRESH_INTERVAL_SECONDS, AWS_REGION, AWS_PROFILE"
  echo "  Run in foreground; use systemd or screen for startup and continuous run."
  exit 1
}

[[ -n "${GITHUB_KEY_SECRET_NAME:-}" ]] || usage
[[ -n "${GITHUB_APP_ID:-}" ]]         || usage
[[ -n "${GITHUB_INSTALLATION_ID:-}" ]] || usage

[[ -x "$CREDENTIAL_HELPER_SCRIPT" ]] || chmod +x "$CREDENTIAL_HELPER_SCRIPT"

TOKEN_DIR="$(dirname "$TOKEN_FILE")"
mkdir -p "$TOKEN_DIR"
chmod 700 "$TOKEN_DIR"

# Ensure git uses the credential helper that reads from our token file.
# Export so the helper sees the same path when invoked by git.
export GIT_AUTH_TOKEN_FILE="$TOKEN_FILE"
git config --global credential.https://github.com.helper "!$CREDENTIAL_HELPER_SCRIPT"
info "Credential helper configured to read from $TOKEN_FILE"

refresh_token() {
  local token
  token=$("$SCRIPT_DIR/git-auth-with-secret.sh" -- bash -c 'printf "%s" "${GH_TOKEN:-}"')
  if [[ -z "$token" ]]; then
    err "Failed to obtain token from git-auth-with-secret.sh"
  fi
  printf '%s' "$token" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  info "Token refreshed and written to $TOKEN_FILE"
}

info "Starting refresh loop (interval ${REFRESH_INTERVAL}s). Press Ctrl+C to stop."
while true; do
  if refresh_token; then
    :
  else
    err "Refresh failed; exiting."
  fi
  sleep "$REFRESH_INTERVAL"
done
