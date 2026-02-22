#!/usr/bin/env bash
#
# Install the git credential refresh daemon as a user systemd service so it runs
# on startup. Call from the directory containing the git scripts (e.g. after
# copying ami/git to /home/ubuntu/desk-git). Run as the ubuntu user (e.g.
# sudo -u ubuntu HOME=/home/ubuntu ./install-git-credential-refresh-service.sh).
#
# Environment:
#   GITHUB_KEY_SECRET_NAME  (optional) Secrets Manager secret name; default: github-desk
#
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[install-git-credential-refresh]${NC} $1"; }
err()  { echo -e "${RED}[install-git-credential-refresh]${NC} $1" >&2; exit 1; }

# Use ubuntu's home if we're in a build/SSM context (no proper $HOME)
if [[ -z "$HOME" || "$HOME" == "/" ]] || [[ "$(whoami)" == "root" && "$HOME" == "/root" ]]; then
  export HOME=/home/ubuntu
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON_SCRIPT="$SCRIPT_DIR/git-credential-refresh-daemon.sh"
[[ -x "$DAEMON_SCRIPT" ]] || err "Daemon script not found or not executable: $DAEMON_SCRIPT"

SECRET_NAME="${GITHUB_KEY_SECRET_NAME:-github-desk}"
USER_CONFIG="$HOME/.config/systemd/user"
mkdir -p "$USER_CONFIG"

SERVICE_FILE="$USER_CONFIG/git-credential-refresh.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Refresh git GitHub App credentials from AWS Secrets Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=GITHUB_KEY_SECRET_NAME=$SECRET_NAME

ExecStart=$DAEMON_SCRIPT
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
EOF

info "Wrote $SERVICE_FILE"

# Allow user systemd services to run at boot without an active session
LINGER_USER="${SUDO_USER:-$USER}"
[[ -z "$LINGER_USER" || "$LINGER_USER" == "root" ]] && LINGER_USER="ubuntu"
if command -v loginctl &>/dev/null; then
  sudo loginctl enable-linger "$LINGER_USER" 2>/dev/null || true
fi

# Enable the service by creating the wants symlink (no D-Bus needed; works during AMI build)
mkdir -p "$USER_CONFIG/default.target.wants"
ln -sf ../git-credential-refresh.service "$USER_CONFIG/default.target.wants/git-credential-refresh.service"
info "Git credential refresh service enabled (starts on boot)."
