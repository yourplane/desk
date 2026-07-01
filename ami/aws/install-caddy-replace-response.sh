#!/usr/bin/env bash
# Install Caddy with the replace-response module (required for session-keeper HTML injection).
set -euo pipefail

if command -v caddy >/dev/null 2>&1 && caddy list-modules 2>/dev/null | grep -q 'http.handlers.replace_response'; then
  echo "Caddy with replace_response already installed: $(caddy version)"
  exit 0
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq golang-go curl ca-certificates

BUILD_USER="${SUDO_USER:-ubuntu}"
BUILD_HOME="$(getent passwd "$BUILD_USER" | cut -d: -f6)"
export GOPATH="${BUILD_HOME}/go"
export PATH="${GOPATH}/bin:${PATH}"

sudo -u "$BUILD_USER" env GOPATH="$GOPATH" PATH="${GOPATH}/bin:${PATH}" \
  go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
sudo -u "$BUILD_USER" env GOPATH="$GOPATH" PATH="${GOPATH}/bin:${PATH}" \
  xcaddy build --with github.com/caddyserver/replace-response --output /tmp/caddy-replace-response

install -m 0755 /tmp/caddy-replace-response /usr/bin/caddy
rm -f /tmp/caddy-replace-response
echo "Installed $(caddy version) with replace_response"
