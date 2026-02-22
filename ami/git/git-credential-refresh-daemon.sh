#!/usr/bin/env bash
#
# Run in the background to keep git credentials up to date. In a loop, fetches
# a fresh GitHub App installation token from the secret and writes it to the
# token file (same as set-git-credentials-from-secret.sh). Git's credential
# helper then serves that token for HTTPS operations to GitHub.
#
# Usage:
#   GITHUB_KEY_SECRET_NAME=my-github-app ./git-credential-refresh-daemon.sh
#
# Run in foreground (Ctrl+C to stop). Use screen, tmux, or systemd to run in the
# background or on startup.
#
# Environment:
#   GITHUB_KEY_SECRET_NAME              (required) Secrets Manager secret name or ID
#   GIT_AUTH_TOKEN_FILE                 (optional) Token file path; default: ~/.config/git-auth/github-token
#   GIT_AUTH_REFRESH_INTERVAL_SECONDS   (optional) Seconds between refreshes; default: 1800 (30 min)
#   AWS_REGION, AWS_PROFILE             (optional) Passed through to set-git-credentials-from-secret.sh
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[git-credential-daemon]${NC} $1"; }
err()  { echo -e "${RED}[git-credential-daemon]${NC} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SET_CREDENTIALS="$SCRIPT_DIR/set-git-credentials-from-secret.sh"

usage() {
  echo "Usage: GITHUB_KEY_SECRET_NAME=<secret> $0"
  echo "  Optional: GIT_AUTH_TOKEN_FILE, GIT_AUTH_REFRESH_INTERVAL_SECONDS (default 1800), AWS_REGION, AWS_PROFILE"
  exit 1
}

[[ -n "${GITHUB_KEY_SECRET_NAME:-}" ]] || usage
[[ -x "$SET_CREDENTIALS" ]] || err "Not found or not executable: $SET_CREDENTIALS"

INTERVAL="${GIT_AUTH_REFRESH_INTERVAL_SECONDS:-1800}"
[[ "$INTERVAL" -gt 0 ]] 2>/dev/null || err "GIT_AUTH_REFRESH_INTERVAL_SECONDS must be a positive number"

info "Starting credential refresh daemon (interval ${INTERVAL}s). Ctrl+C to stop."

while true; do
  if "$SET_CREDENTIALS"; then
    info "Next refresh in ${INTERVAL}s"
  else
    err "Refresh failed; exiting"
  fi
  sleep "$INTERVAL"
done
