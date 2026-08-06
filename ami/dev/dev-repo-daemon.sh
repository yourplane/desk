#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info() { echo -e "${GREEN}[dev-repo-daemon]${NC} $1"; }
warn() { echo -e "${YELLOW}[dev-repo-daemon]${NC} $1" >&2; }
err() { echo -e "${RED}[dev-repo-daemon]${NC} $1" >&2; exit 1; }

if [[ -z "${HOME:-}" || "${HOME:-}" == "/" ]]; then
  export HOME=/home/ubuntu
fi

REPO_URL="${DEV_REPO_URL:-https://github.com/yourplane/dev.git}"
REPO_DIR="${DEV_REPO_DIR:-$HOME/.local/share/desk/dev}"

mkdir -p "$(dirname "$REPO_DIR")"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  info "Cloning ${REPO_URL} into ${REPO_DIR}"
  git clone "$REPO_URL" "$REPO_DIR"
else
  info "Updating ${REPO_DIR}"
  git -C "$REPO_DIR" fetch --all --prune || warn "git fetch failed; continuing with existing checkout"
  if ! git -C "$REPO_DIR" pull --ff-only; then
    warn "git pull failed; continuing with existing checkout"
  fi
fi

[[ -x "$REPO_DIR/daemon" ]] || err "Expected executable not found: $REPO_DIR/daemon"
info "Starting repo daemon from ${REPO_DIR}"
exec "$REPO_DIR/daemon"
