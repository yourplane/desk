#!/usr/bin/env bash
#
# Loop: refresh every bot in bots.json (see set-git-credentials-from-secret.sh).
# If one secret fails, others still refresh; the daemon keeps running.
#
# Usage:
#   ./git-credential-refresh-daemon.sh
#
# Environment:
#   GITHUB_KEY_SECRET_CONFIG              Path to bots JSON (default: ~/.config/git-auth/bots.json)
#   GIT_AUTH_REFRESH_INTERVAL_SECONDS     Seconds between refreshes; default: 1800 (30 min)
#   AWS_REGION, AWS_PROFILE               Passed through to set-git-credentials-from-secret.sh
#
set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}[git-credential-daemon]${NC} $1"; }
warn() { echo -e "${YELLOW}[git-credential-daemon]${NC} $1" >&2; }
err()  { echo -e "${RED}[git-credential-daemon]${NC} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SET_CREDENTIALS="$SCRIPT_DIR/set-git-credentials-from-secret.sh"

[[ -x "$SET_CREDENTIALS" ]] || err "Not found or not executable: $SET_CREDENTIALS"

INTERVAL="${GIT_AUTH_REFRESH_INTERVAL_SECONDS:-1800}"
[[ "$INTERVAL" -gt 0 ]] 2>/dev/null || err "GIT_AUTH_REFRESH_INTERVAL_SECONDS must be a positive number"

info "Starting credential refresh daemon (interval ${INTERVAL}s). Ctrl+C to stop."

while true; do
  if "$SET_CREDENTIALS"; then
    info "Next refresh in ${INTERVAL}s"
  else
    warn "Refresh cycle had failures (see logs above); retrying after ${INTERVAL}s"
  fi
  sleep "$INTERVAL"
done
