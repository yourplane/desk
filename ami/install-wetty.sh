#!/bin/bash
#
# Wetty Installation Script
# Installs wetty web terminal on Ubuntu with proper Node.js version
#
# Usage: ./install-wetty.sh
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
        info "nvm already installed"
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
    info "Installing Node.js $NODE_VERSION via nvm..."
    nvm install "$NODE_VERSION"
    nvm use "$NODE_VERSION"
    
    echo ""
    info "Node.js version: $(node --version)"
    info "npm version: $(npm --version)"
}

# Install wetty
install_wetty() {
    info "Installing wetty@$WETTY_VERSION..."
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
        info "SSH key already exists at $SSH_KEY_PATH"
    else
        info "Generating SSH key for wetty..."
        ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "wetty-key"
        
        # Add to authorized_keys
        cat "${SSH_KEY_PATH}.pub" >> "$HOME/.ssh/authorized_keys"
        chmod 600 "$HOME/.ssh/authorized_keys"
        
        info "SSH key generated and added to authorized_keys"
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

# Create systemd service (optional)
create_systemd_service() {
    local service_file="/etc/systemd/system/wetty.service"
    local nvm_node="$HOME/.nvm/versions/node/v$(nvm version "$NODE_VERSION" | tr -d 'v')/bin"
    local wetty_cmd=$(build_wetty_command "$nvm_node")
    
    info "Creating systemd service..."
    
    sudo tee "$service_file" > /dev/null << EOF
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

    sudo systemctl daemon-reload
    info "Systemd service created at $service_file"
    echo ""
    info "To enable and start wetty service:"
    echo "  sudo systemctl enable wetty"
    echo "  sudo systemctl start wetty"
}

# Print manual start instructions
print_instructions() {
    echo ""
    echo "=========================================="
    echo -e "${GREEN}Installation Complete!${NC}"
    echo "=========================================="
    echo ""
    
    if [ "$TUNNEL_MODE" = "true" ]; then
        echo "Mode: TUNNEL (no authentication, localhost only)"
        echo ""
        echo "To start wetty manually:"
        echo ""
        echo "  # Load nvm first"
        echo "  export NVM_DIR=\"\$HOME/.nvm\""
        echo "  [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\""
        echo ""
        echo "  # Start wetty (tunnel mode - SSH key auth, no login prompt)"
        echo "  wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH --ssh-auth publickey"
        echo ""
        echo "Or run in background:"
        echo "  nohup wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH --ssh-auth publickey > /tmp/wetty.log 2>&1 &"
        echo ""
        echo "Connect via SSH tunnel from your local machine:"
        echo "  ssh -L $WETTY_PORT:127.0.0.1:$WETTY_PORT user@your-server"
        echo ""
        echo "Then access wetty at: http://localhost:$WETTY_PORT$WETTY_BASE"
    else
        echo "Mode: SSH (key-based authentication)"
        echo ""
        echo "To start wetty manually:"
        echo ""
        echo "  # Load nvm first"
        echo "  export NVM_DIR=\"\$HOME/.nvm\""
        echo "  [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\""
        echo ""
        echo "  # Start wetty"
        echo "  wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH"
        echo ""
        echo "Or run in background:"
        echo "  nohup wetty --host 127.0.0.1 --port $WETTY_PORT --base $WETTY_BASE --ssh-host $SSH_HOST --ssh-user $SSH_USER --ssh-key $SSH_KEY_PATH > /tmp/wetty.log 2>&1 &"
        echo ""
        echo "Access wetty at: http://localhost:$WETTY_PORT$WETTY_BASE"
    fi
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
    read -p "Create systemd service for auto-start? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        create_systemd_service
    fi
    
    print_instructions
}

main "$@"
