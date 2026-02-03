#!/bin/bash
#
# Wetty Installation Script
# Installs wetty web terminal on Ubuntu with proper Node.js version
#
# Usage: ./install-wetty.sh
#
# This script is idempotent - safe to run multiple times.
# It will skip steps that have already been completed.
#

set -e

echo "=== Wetty Installation Script ==="
echo ""

# Configuration
WETTY_VERSION="2.5.0"  # 2.7.0 has broken client-side JS bundling
NODE_VERSION="20"
SSH_KEY_PATH="$HOME/.ssh/wetty_key"
SSH_USER="$USER"
SSH_HOST="localhost"
WETTY_PORT="3000"
WETTY_BASE="/wetty"

# Tunnel mode: no authentication, binds to localhost only
# Safe when accessed via SSH tunnel (already authenticated/encrypted)
TUNNEL_MODE="true"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    error "Please run as a regular user, not root"
fi

# Install nvm if not present
install_nvm() {
    if [ -d "$HOME/.nvm" ]; then
        info "nvm already installed, skipping"
    else
        info "Installing nvm..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    fi
    
    # Load nvm
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
}

# Install Node.js via nvm
install_node() {
    # Check if the required Node.js version is already installed
    if nvm ls "$NODE_VERSION" &>/dev/null && nvm ls "$NODE_VERSION" | grep -q "v$NODE_VERSION"; then
        info "Node.js $NODE_VERSION already installed, skipping"
        nvm use "$NODE_VERSION"
    else
        info "Installing Node.js $NODE_VERSION via nvm..."
        nvm install "$NODE_VERSION"
        nvm use "$NODE_VERSION"
    fi
    
    echo ""
    info "Node.js version: $(node --version)"
    info "npm version: $(npm --version)"
}

# Install wetty
install_wetty() {
    # Check if wetty is already installed at the correct version
    if command -v wetty &>/dev/null; then
        local installed_version
        installed_version=$(npm list -g wetty 2>/dev/null | grep wetty@ | sed 's/.*wetty@//' || echo "")
        if [ "$installed_version" = "$WETTY_VERSION" ]; then
            info "wetty@$WETTY_VERSION already installed, skipping"
            return
        else
            info "Updating wetty from $installed_version to $WETTY_VERSION..."
        fi
    else
        info "Installing wetty@$WETTY_VERSION..."
    fi
    
    npm install -g "wetty@$WETTY_VERSION"
    
    # Verify installation
    if command -v wetty &> /dev/null; then
        info "Wetty installed successfully"
    else
        error "Wetty installation failed"
    fi
}

# Setup SSH key for passwordless auth
setup_ssh_key() {
    if [ -f "$SSH_KEY_PATH" ]; then
        info "SSH key already exists at $SSH_KEY_PATH, skipping generation"
    else
        info "Generating SSH key for wetty..."
        ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "wetty-key"
        info "SSH key generated"
    fi
    
    # Ensure authorized_keys exists
    mkdir -p "$HOME/.ssh"
    touch "$HOME/.ssh/authorized_keys"
    chmod 600 "$HOME/.ssh/authorized_keys"
    
    # Add public key to authorized_keys if not already present
    local pubkey
    pubkey=$(cat "${SSH_KEY_PATH}.pub")
    if grep -qF "$pubkey" "$HOME/.ssh/authorized_keys" 2>/dev/null; then
        info "SSH public key already in authorized_keys, skipping"
    else
        echo "$pubkey" >> "$HOME/.ssh/authorized_keys"
        info "SSH public key added to authorized_keys"
    fi
    
    # Test SSH connection
    info "Testing SSH connection..."
    if ssh -i "$SSH_KEY_PATH" -o BatchMode=yes -o ConnectTimeout=5 "$SSH_USER@$SSH_HOST" echo "SSH OK" &>/dev/null; then
        info "SSH connection test passed"
    else
        warn "SSH connection test failed - you may need to configure SSH manually"
    fi
}

# Build wetty command based on mode
build_wetty_command() {
    local node_bin="$1"
    
    if [ "$TUNNEL_MODE" = "true" ]; then
        # Tunnel mode: SSH key auth, localhost only, no login prompt
        # --ssh-auth publickey is REQUIRED for key-based auth to work
        echo "$node_bin/wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH --ssh-auth publickey"
    else
        # SSH mode: uses SSH key authentication (still prompts for username)
        echo "$node_bin/wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH --ssh-auth publickey"
    fi
}

# Create systemd service for auto-start
create_systemd_service() {
    local service_file="/etc/systemd/system/wetty.service"
    local nvm_node="$HOME/.nvm/versions/node/v$(nvm version "$NODE_VERSION" | tr -d 'v')/bin"
    local wetty_cmd=$(build_wetty_command "$nvm_node")
    
    # Build the expected service content
    local expected_content
    expected_content=$(cat << EOF
[Unit]
Description=Wetty Web Terminal
After=network.target

[Service]
Type=simple
User=$USER
Environment=PATH=$nvm_node:/usr/bin:/bin
ExecStart=$wetty_cmd
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
)
    
    # Check if service file already exists with correct content
    if [ -f "$service_file" ]; then
        local current_content
        current_content=$(sudo cat "$service_file" 2>/dev/null || echo "")
        if [ "$current_content" = "$expected_content" ]; then
            info "Systemd service already configured correctly, skipping"
        else
            info "Updating systemd service..."
            echo "$expected_content" | sudo tee "$service_file" > /dev/null
            sudo systemctl daemon-reload
            info "Systemd service updated"
        fi
    else
        info "Creating systemd service..."
        echo "$expected_content" | sudo tee "$service_file" > /dev/null
        sudo systemctl daemon-reload
        info "Systemd service created at $service_file"
    fi
}

# Enable wetty service for auto-start on boot
enable_systemd_service() {
    if systemctl is-enabled wetty &>/dev/null; then
        info "Wetty service already enabled for auto-start, skipping"
    else
        info "Enabling wetty service for auto-start..."
        sudo systemctl enable wetty
        info "Wetty service enabled"
    fi
}

# Start wetty (via systemd if available, otherwise in background)
start_wetty() {
    # Check if wetty is already running
    if systemctl is-active wetty &>/dev/null; then
        info "Wetty is already running via systemd"
        return
    fi
    
    if pgrep -f "wetty.*--port $WETTY_PORT" &>/dev/null; then
        info "Wetty is already running in background"
        return
    fi
    
    # Start via systemd
    info "Starting wetty via systemd..."
    sudo systemctl start wetty
    
    # Verify it started
    sleep 2
    if systemctl is-active wetty &>/dev/null; then
        info "Wetty started successfully"
    else
        warn "Failed to start wetty via systemd, check: sudo systemctl status wetty"
    fi
}

# Print status and access instructions
print_instructions() {
    echo ""
    echo "=========================================="
    echo -e "${GREEN}Wetty Setup Complete!${NC}"
    echo "=========================================="
    echo ""
    
    if [ "$TUNNEL_MODE" = "true" ]; then
        echo "Mode: TUNNEL (no authentication, localhost only)"
    else
        echo "Mode: SSH (key-based authentication)"
    fi
    echo ""
    echo "Wetty is configured to start automatically on boot."
    echo ""
    echo "Service management:"
    echo "  sudo systemctl status wetty   # Check status"
    echo "  sudo systemctl restart wetty  # Restart"
    echo "  sudo systemctl stop wetty     # Stop"
    echo "  journalctl -u wetty -f        # View logs"
    echo ""
    echo "Connect via SSH tunnel from your local machine:"
    echo "  ssh -L $WETTY_PORT:127.0.0.1:$WETTY_PORT user@your-server"
    echo ""
    echo "Then access wetty at: http://localhost:$WETTY_PORT$WETTY_BASE"
    echo ""
}

# Main installation flow
main() {
    if [ "$TUNNEL_MODE" = "true" ]; then
        info "Installing in TUNNEL mode (no authentication, localhost only)"
        warn "Only use this mode when accessing via SSH tunnel!"
    else
        info "Installing in SSH mode (key-based authentication)"
    fi
    echo ""
    
    install_nvm
    
    # Reload nvm in case it was just installed
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    
    install_node
    install_wetty
    setup_ssh_key
    
    echo ""
    create_systemd_service
    enable_systemd_service
    start_wetty
    
    print_instructions
}

main "$@"
