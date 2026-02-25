#!/usr/bin/env bash
# Install AWS Session Manager plugin on Ubuntu (x86_64).
# Required for 'aws ssm start-session'. Safe to run multiple times (idempotent).
set -e

if command -v session-manager-plugin &>/dev/null; then
  echo "Session Manager plugin already installed: $(session-manager-plugin --version 2>/dev/null || true)"
  exit 0
fi

echo "Installing dependencies (curl)..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl

echo "Installing AWS Session Manager plugin..."
cd /tmp
curl -sS "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o session-manager-plugin.deb
sudo dpkg -i session-manager-plugin.deb
rm -f session-manager-plugin.deb
echo "Done: $(session-manager-plugin --version 2>/dev/null || true)"
