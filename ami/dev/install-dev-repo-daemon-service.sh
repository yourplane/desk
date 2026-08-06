#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[install-dev-repo-daemon]${NC} $1"; }
err() { echo -e "${RED}[install-dev-repo-daemon]${NC} $1" >&2; exit 1; }

if [[ -z "${HOME:-}" || "${HOME:-}" == "/" ]] || [[ "$(whoami)" == "root" && "${HOME:-}" == "/root" ]]; then
  export HOME=/home/ubuntu
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SCRIPT="$SCRIPT_DIR/dev-repo-daemon.sh"
[[ -x "$DAEMON_SCRIPT" ]] || err "Daemon wrapper script not executable: $DAEMON_SCRIPT"

USER_CONFIG="$HOME/.config/systemd/user"
mkdir -p "$USER_CONFIG"

SERVICE_FILE="$USER_CONFIG/dev-repo-daemon.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Run github.com/yourplane/dev daemon at startup
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$DAEMON_SCRIPT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

info "Wrote $SERVICE_FILE"

LINGER_USER="${SUDO_USER:-$USER}"
[[ -z "$LINGER_USER" || "$LINGER_USER" == "root" ]] && LINGER_USER="ubuntu"
if command -v loginctl >/dev/null 2>&1; then
  sudo loginctl enable-linger "$LINGER_USER" 2>/dev/null || true
fi

mkdir -p "$USER_CONFIG/default.target.wants"
ln -sf ../dev-repo-daemon.service "$USER_CONFIG/default.target.wants/dev-repo-daemon.service"

info "Dev repo daemon service enabled (starts on boot)."
