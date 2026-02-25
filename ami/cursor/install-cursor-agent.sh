#!/usr/bin/env bash
# Install Cursor CLI agent on Ubuntu (x86_64).
# Configures agent to use the auto model, trust every workspace by default, and always allow everything (--force / Run Everything).
# Safe to run multiple times (idempotent).
set -e

CURSOR_DEB_URL="https://api2.cursor.sh/updates/download/golden/linux-x64-deb/cursor/2.5"

if command -v cursor &>/dev/null && cursor --help &>/dev/null; then
  echo "Cursor already installed."
else
  echo "Installing dependencies..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  # Cursor .deb deps; on Ubuntu 24.04 some packages use t64 suffix (e.g. libasound2t64)
  apt-get install -y -qq curl ca-certificates \
    libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libgbm1 \
    libgtk-3-0 libpango-1.0-0 libxcomposite1 libxdamage1 libxfixes3 libxkbfile1 \
    libxrandr2 xdg-utils \
    libasound2t64 2>/dev/null || apt-get install -y -qq libasound2

  echo "Downloading Cursor..."
  cd /tmp
  curl -sL -o cursor.deb "$CURSOR_DEB_URL"

  echo "Installing Cursor..."
  dpkg -i cursor.deb || true
  apt-get install -f -y -qq
  rm -f cursor.deb
  echo "Cursor installed."
fi

# Ensure /usr/local/bin exists and add agent wrapper: auto model, trust workspace, always allow everything
AGENT_WRAPPER="/usr/local/bin/agent"
if [[ ! -x "$AGENT_WRAPPER" ]] || ! grep -q "cursor agent" "$AGENT_WRAPPER" 2>/dev/null; then
  echo "Configuring agent: auto model, trust workspace by default, always allow everything..."
  mkdir -p /usr/local/bin
  cat > "$AGENT_WRAPPER" << 'WRAPPER'
#!/bin/sh
# Cursor CLI agent wrapper: auto model, trust workspace, Run Everything mode.
exec /usr/bin/cursor agent --model auto --trust --force "$@"
WRAPPER
  chmod 755 "$AGENT_WRAPPER"
  echo "Agent wrapper installed at $AGENT_WRAPPER"
fi

# Add /usr/local/bin to ubuntu user's PATH in .bashrc if not already there
UBUNTU_BASHRC="/home/ubuntu/.bashrc"
if [[ -f "$UBUNTU_BASHRC" ]] && ! grep -q '/usr/local/bin' "$UBUNTU_BASHRC" 2>/dev/null; then
  echo 'export PATH="/usr/local/bin:$PATH"' >> "$UBUNTU_BASHRC"
  chown ubuntu:ubuntu "$UBUNTU_BASHRC"
fi

echo "Cursor CLI agent install complete (auto model, trust workspace, Run Everything). Use: agent [prompt...]"
