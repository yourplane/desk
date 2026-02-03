# Wetty Installation Guide

Wetty is a web-based terminal emulator that allows you to access a shell session through your browser via SSH.

## Quick Start

```bash
./install-wetty.sh
```

Then access wetty at: `http://localhost:3000/wetty`

---

## Challenges & Findings

This document describes the issues encountered while setting up wetty on Ubuntu and the solutions found.

### Problem 1: Black Screen in Browser

**Symptom:** Wetty page loads but shows a black screen with no terminal.

**Browser Console Error:**
```
Uncaught TypeError: Failed to resolve module specifier "@fortawesome/fontawesome-svg-core". 
Relative references must start with either "/", "./", or "../".
```

**Root Cause:** Wetty 2.7.0 (latest) has broken client-side JavaScript. The frontend code uses bare module specifiers like `@fortawesome/fontawesome-svg-core` which browsers cannot resolve without an import map or bundler.

**Evidence:**
```javascript
// wetty 2.7.0 client/wetty.js - BROKEN
import {dom, library} from "@fortawesome/fontawesome-svg-core";
```

```javascript
// wetty 2.5.0 client/wetty.js - WORKS
import {dom, library} from "../web_modules/pkg/@fortawesome/fontawesome-svg-core.js";
```

**Solution:** Use wetty version 2.5.0 which has properly bundled client assets with relative imports.

---

### Problem 2: Native Module Version Mismatch

**Symptom:** Wetty crashes on startup with:
```
Error: The module '.../gc-stats/build/Release/gcstats.node'
was compiled against a different Node.js version using
NODE_MODULE_VERSION 108. This version of Node.js requires
NODE_MODULE_VERSION 109.
```

**Root Cause:** Ubuntu's packaged Node.js 18.19.1 (`18.19.1+dfsg-6ubuntu5`) has been compiled with a non-standard `NODE_MODULE_VERSION` of 109 instead of the expected 108 for Node.js 18.x.

```bash
$ node --version
v18.19.1

$ node -p "process.versions.modules"
109  # Should be 108 for Node 18.x!
```

This causes all native modules (gc-stats, node-pty) installed via npm to fail because:
1. npm downloads prebuilt binaries compiled for standard Node 18 (module version 108)
2. Ubuntu's Node expects module version 109
3. Rebuilding with `npm rebuild` downloads headers from nodejs.org which define version 108

**Affected Modules:**
- `gc-stats` - garbage collection monitoring (optional)
- `node-pty` - pseudo-terminal support (required)

**Solution:** Use nvm to install a standard Node.js build from nodejs.org instead of the Ubuntu package.

---

### Problem 3: npm rebuild Doesn't Fix Native Modules

**Symptom:** Running `npm rebuild` reports success but modules still fail to load.

**Root Cause:** `npm rebuild` uses cached headers from `~/.cache/node-gyp/` or `/root/.cache/node-gyp/`. These headers are downloaded from nodejs.org and define the standard module version (108), not the Ubuntu-modified version (109).

**Attempted Fixes That Failed:**
```bash
# These all failed due to header mismatch
sudo npm rebuild
cd node_modules/gc-stats && sudo npm rebuild
sudo rm -rf /root/.cache/node-gyp && sudo npm rebuild
```

**Solution:** Don't try to fix the system Node.js - use nvm instead.

---

### Problem 4: Removing gc-stats Breaks Wetty

**Symptom:** After removing gc-stats to bypass the module error:
```
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'gc-stats' 
imported from /usr/local/lib/node_modules/wetty/build/server.js
```

**Root Cause:** Wetty has a hard dependency on gc-stats, not an optional one.

**Attempted Fix:** Creating a stub module:
```javascript
// Stub gc-stats - doesn't fully work
const EventEmitter = require('events');
module.exports = function() {
  return new EventEmitter();
};
```

This bypassed gc-stats but then node-pty had the same module version issue.

**Solution:** Fix the root cause (Node.js version) instead of patching individual modules.

---

## Working Installation Steps

### 1. Install nvm

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

### 2. Install Node.js 20

```bash
nvm install 20
nvm use 20
```

Verify correct module version:
```bash
$ node -p "process.versions.modules"
115  # Correct for Node 20
```

### 3. Install Wetty 2.5.0

```bash
npm install -g wetty@2.5.0
```

**Important:** Do NOT install latest (2.7.0) - it has broken client bundling.

### 4. Setup SSH Key

```bash
ssh-keygen -t ed25519 -f ~/.ssh/wetty_key -N "" -C "wetty-key"
cat ~/.ssh/wetty_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### 5. Start Wetty

```bash
wetty --host 127.0.0.1 --port 3000 --base /wetty \
  --ssh-host localhost --ssh-user $USER --ssh-key ~/.ssh/wetty_key \
  --ssh-auth publickey
```

Access at: `http://localhost:3000/wetty`

---

## SSH Tunnel Mode (No Browser Login)

If you're accessing wetty through an SSH tunnel, you can configure it to skip the login prompt entirely. Since the SSH tunnel already provides authentication and encryption, wetty doesn't need additional credentials.

### Configuration

Start wetty with these options:

```bash
wetty \
  --host 127.0.0.1 \
  --port 3000 \
  --base /wetty \
  --ssh-host localhost \
  --ssh-user $USER \
  --ssh-key ~/.ssh/wetty_key \
  --ssh-auth publickey
```

Key options:
- `--host 127.0.0.1` - Only listen on localhost (not accessible from network)
- `--ssh-key ~/.ssh/wetty_key` - Use SSH key for authentication
- `--ssh-auth publickey` - **Required** to use key-based auth (defaults to "password" otherwise)

### Connecting via SSH Tunnel

From your local machine:

```bash
# Create SSH tunnel
ssh -L 3000:127.0.0.1:3000 user@your-server

# Then open in browser
http://localhost:3000/wetty
```

You'll be dropped directly into a shell with no login prompt.

### Why `--ssh-auth publickey` is Required

Even with `--ssh-key` specified, wetty defaults to password authentication. Without `--ssh-auth publickey`, you'll see:

```
Authentication Type: password
```

And get "Permission denied (publickey)" errors. With the flag:

```
Authentication Type: publickey
```

And the connection succeeds without prompting for credentials.

---

## Version Compatibility Matrix

| Wetty Version | Client Bundling | Status |
|---------------|-----------------|--------|
| 2.7.0         | Broken (bare imports) | Do not use |
| 2.6.x         | Unknown | Not tested |
| 2.5.0         | Working (relative imports) | Recommended |

| Node.js Source | Module Version | Native Modules |
|----------------|----------------|----------------|
| Ubuntu 24.04 package (18.19.1+dfsg) | 109 (non-standard) | Broken |
| nvm / nodejs.org (18.x) | 108 | Works |
| nvm / nodejs.org (20.x) | 115 | Works |

---

## Running as a Service

Create `/etc/systemd/system/wetty.service`:

```ini
[Unit]
Description=Wetty Web Terminal
After=network.target

[Service]
Type=simple
User=ubuntu
Environment=PATH=/home/ubuntu/.nvm/versions/node/v20.20.0/bin:/usr/bin:/bin
ExecStart=/home/ubuntu/.nvm/versions/node/v20.20.0/bin/wetty --host 127.0.0.1 --port 3000 --base /wetty --ssh-host localhost --ssh-user ubuntu --ssh-key /home/ubuntu/.ssh/wetty_key --ssh-auth publickey
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable wetty
sudo systemctl start wetty
```

---

## Troubleshooting

### Check if wetty is running
```bash
ps aux | grep wetty
curl -s http://localhost:3000/wetty | head -5
```

### Test SSH key authentication
```bash
ssh -i ~/.ssh/wetty_key -o BatchMode=yes localhost echo "SSH works"
```

### Check for module errors
```bash
wetty --ssh-host localhost --ssh-user $USER --ssh-key ~/.ssh/wetty_key 2>&1 | head -20
```

### Verify Node.js module version
```bash
node -p "process.versions.modules"
```

### Check wetty client JS for bare imports (broken)
```bash
head -3 $(npm root -g)/wetty/build/client/wetty.js
# If you see: import ... from "@fortawesome/..." - it's broken
# If you see: import ... from "../web_modules/..." - it's working
```

---

## Security Notes

- Wetty with `--ssh-key` and `--ssh-auth publickey` enables passwordless authentication
- Anyone who can reach the wetty port can get shell access
- **Recommended:** Use `--host 127.0.0.1` to bind only to localhost, then access via SSH tunnel
- SSH tunneling provides encryption and authentication without needing HTTPS
- If exposing directly, consider adding `--ssl-key` and `--ssl-cert` for HTTPS

---

## References

- [Wetty GitHub](https://github.com/butlerx/wetty)
- [nvm GitHub](https://github.com/nvm-sh/nvm)
- [Node.js MODULE_VERSION table](https://nodejs.org/en/download/releases/)
