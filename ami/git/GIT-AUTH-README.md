# Git credentials with AWS Secrets Manager

Scripts in `ami/git/` manage **multiple** GitHub App credentials: each AWS Secrets Manager secret (app id, installation id, private key) is listed in `~/.config/git-auth/bots.json` together with the **GitHub org** whose repositories should use that installation token.

## 1. Create secrets

**`create-github-app-secret.sh`** — Store app id, installation id, and private key in one Secrets Manager secret per app/installation.

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

## 2. Configure bots (`bots.json`)

Create or edit **`~/.config/git-auth/bots.json`** (or use `bots.json.example` as a template). Each entry maps one AWS secret to one GitHub org (the first path segment of `https://github.com/<org>/...`).

```json
{
  "bots": [
    { "secret": "YOUR_SECRET", "org": "acme" },
    { "secret": "YOUR_OTHER_SECRET", "org": "widgets-inc" }
  ]
}
```

| Field | Description |
|-------|-------------|
| `secret` | Secrets Manager secret name or ID (JSON with `app_id`, `installation_id`, `private_key`) |
| `org` | GitHub organization name (case-insensitive match against the URL) |

**IAM:** the instance user needs `secretsmanager:GetSecretValue` on **each** secret.

## 3. Apply credentials (one-shot)

**`set-git-credentials-from-secret.sh`** — For every entry in `bots.json`, fetch the secret, mint an installation token, write it under `~/.config/git-auth/tokens/<org>`, and install the **`git-credential-github-dispatch.sh`** helper so HTTPS operations to `https://github.com/<org>/...` use the right token.

```bash
./set-git-credentials-from-secret.sh
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_KEY_SECRET_CONFIG` | No | Path to `bots.json` (default: `~/.config/git-auth/bots.json`) |
| `AWS_REGION`, `AWS_PROFILE` | No | Passed to `aws` CLI |

Requirements: `aws` CLI, `jq`, `openssl`, `curl`. For token retrieval, `npx` is recommended; otherwise a built-in JWT + `curl` fallback is used.

The active config path is written to `~/.config/git-auth/config.path` so the credential helper (invoked by Git without your shell environment) resolves the same `bots.json` as the refresh scripts.

**Token expiry:** Installation tokens expire (typically after about one hour). Re-run this script to refresh, or use the daemon below.

## 4. Keep credentials refreshed (daemon)

**`git-credential-refresh-daemon.sh`** — Runs in a loop and re-runs `set-git-credentials-from-secret.sh` on an interval. If **one** secret fails (missing, invalid, etc.), the others still refresh and the daemon **keeps running**.

```bash
./git-credential-refresh-daemon.sh
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_KEY_SECRET_CONFIG` | No | Same as step 3 |
| `GIT_AUTH_REFRESH_INTERVAL_SECONDS` | No | Seconds between refreshes (default: 1800) |
| `AWS_REGION`, `AWS_PROFILE` | No | Passed through |

### Run on startup (systemd user)

**`install-git-credential-refresh-service.sh`** installs a user unit and copies `bots.json.example` to `~/.config/git-auth/bots.json` if missing. Edit `org` and add more `bots` entries as needed.

Manual install:

```bash
mkdir -p ~/.config/systemd/user
cp ami/git/git-credential-refresh.service ~/.config/systemd/user/
# Edit: ExecStart path and Environment=GITHUB_KEY_SECRET_CONFIG
systemctl --user daemon-reload
systemctl --user enable --now git-credential-refresh.service
```

Logs: `journalctl --user -u git-credential-refresh.service -f`

## Security

- Tokens are written with mode `600` under `~/.config/git-auth/tokens/`.
- The private key is not persisted by these scripts (it is fetched from Secrets Manager, used in memory, then discarded).
- The dispatcher helper only responds for `host=github.com` and orgs listed in `bots.json`.

## Troubleshooting

- **403 / authentication failed** for a repo: check that repo’s org matches a `bots.json` entry (including spelling), that the installation has access to that org, and that the token file exists for that org after a successful refresh.
- **Wrong org** receives a credential: confirm the URL path (`github.com/<org>/...`) matches the configured `org`.
