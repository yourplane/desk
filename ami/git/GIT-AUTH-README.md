# Git credentials with AWS Secrets Manager

Scripts in `ami/git/` manage GitHub App credentials via a single AWS Secrets Manager secret (app id, installation id, and private key).

## 1. Create the secret

**`create-github-app-secret.sh`** — Store app id, installation id, and private key in one Secrets Manager secret.

```bash
./create-github-app-secret.sh --secret-name my-github-app \
  --app-id 123456 --installation-id 789012 \
  --private-key-file /path/to/private-key.pem
```

| Option | Required | Description |
|--------|----------|-------------|
| `--secret-name` | Yes | Secrets Manager secret name or ID |
| `--app-id` | Yes | GitHub App ID |
| `--installation-id` | Yes | GitHub App installation ID |
| `--private-key-file` | Yes | Path to the PEM file |
| `--region` | No | AWS region |
| `--profile` | No | AWS CLI profile |

Requires: `aws` CLI, `python3`. Creates the secret if it does not exist, or updates it if it does.

## 2. Set global git credentials from the secret

**`set-git-credentials-from-secret.sh`** — Fetch the secret, obtain a GitHub installation token, and set git’s global credential helper so all git HTTPS operations to GitHub use that token.

```bash
GITHUB_KEY_SECRET_NAME=my-github-app ./set-git-credentials-from-secret.sh
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_KEY_SECRET_NAME` | Yes | Secrets Manager secret name or ID (must contain `app_id`, `installation_id`, `private_key`) |
| `GIT_AUTH_TOKEN_FILE` | No | Where to write the token (default: `~/.config/git-auth/github-token`) |
| `AWS_REGION` | No | AWS region |
| `AWS_PROFILE` | No | AWS CLI profile |

The script writes the token to the token file and sets `credential.https://github.com.helper` so any `git clone`, `git pull`, or `git push` to `https://github.com` uses it.

**Requirements:** `aws` CLI, `jq`, `openssl`, `curl`. For token retrieval, `npx` is recommended; otherwise a built-in JWT + curl fallback is used.

**Token expiry:** GitHub App installation tokens expire (typically after 1 hour). Re-run the script to refresh.

## 3. Keep credentials refreshed (daemon)

**`git-credential-refresh-daemon.sh`** — Runs in a loop: fetches a new token and writes it to the token file (same path as step 2), so git always has a valid token without re-running the one-shot script.

```bash
GITHUB_KEY_SECRET_NAME=my-github-app ./git-credential-refresh-daemon.sh
```

Runs in the foreground; use Ctrl+C to stop. For background or startup, run it inside **screen** or **tmux**, or install the systemd user service below.

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_KEY_SECRET_NAME` | Yes | Same as set-git-credentials-from-secret.sh |
| `GIT_AUTH_TOKEN_FILE` | No | Same default as step 2 |
| `GIT_AUTH_REFRESH_INTERVAL_SECONDS` | No | Seconds between refreshes (default: 1800 = 30 min) |
| `AWS_REGION`, `AWS_PROFILE` | No | Passed through to the one-shot script |

### Run on startup (systemd user)

1. Copy and edit the unit file:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ami/git/git-credential-refresh.service ~/.config/systemd/user/
   # Edit: set GITHUB_KEY_SECRET_NAME and the ExecStart path to your ami/git directory.
   ```
2. Enable and start:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now git-credential-refresh.service
   ```

Logs: `journalctl --user -u git-credential-refresh.service -f`

## AWS permissions

- **create-github-app-secret.sh:** `secretsmanager:CreateSecret`, `secretsmanager:PutSecretValue` (and `DescribeSecret` to detect existing secret).
- **set-git-credentials-from-secret.sh** and **git-credential-refresh-daemon.sh:** `secretsmanager:GetSecretValue`.

## Security

- The token is written to the token file with mode `600`; only the credential helper (invoked by git) reads it.
- The private key is never written to disk by these scripts (it is fetched, used in memory to obtain a token, then discarded).
