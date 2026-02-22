# Git auth with Secrets Manager

`git-auth-with-secret.sh` fetches a GitHub private key from AWS Secrets Manager and configures the environment so `git` can authenticate with GitHub.

## Creating a GitHub App secret

To store **app id**, **installation id**, and **private key** in a single Secrets Manager secret (so you only need `GITHUB_KEY_SECRET_NAME` when using the auth script):

```bash
./create-github-app-secret.sh --secret-name my-github-app \
  --app-id 123456 --installation-id 789012 \
  --private-key-file /path/to/private-key.pem
```

Optional: `--region`, `--profile`. Requires `aws` CLI and `python3`. Creates the secret if it does not exist, or updates it if it does.

## Set global git credentials from secret

To pull **app id**, **installation id**, and **private key** from a single secret, obtain a GitHub installation token, and set git’s **global** credential helper so all git commands (in any terminal) use that token for `https://github.com`:

```bash
GITHUB_KEY_SECRET_NAME=my-github-app ./set-git-credentials-from-secret.sh
```

The script writes the token to `~/.config/git-auth/github-token` and sets `credential.https://github.com.helper` to use `ami/git/git-credential-helper.sh`. GitHub App tokens expire (typically after 1 hour); re-run the script to refresh, or use the credential refresh daemon for automatic refresh.

## Requirements

- **AWS CLI** configured (credentials and region) with permission to `secretsmanager:GetSecretValue` (and `create-secret`/`put-secret-value` if using `create-github-app-secret.sh`)
- **Secret**: Stored in Secrets Manager; value can be:
  - Raw PEM or SSH private key string, or
  - JSON with a `private_key` or `key` field (e.g. `{"private_key":"-----BEGIN RSA PRIVATE KEY-----\n..."}`). For JSON, `jq` is recommended.
  - For GitHub App: JSON with `app_id`, `installation_id`, and `private_key` (as created by `create-github-app-secret.sh`). Then you do not need to set `GITHUB_APP_ID` or `GITHUB_INSTALLATION_ID` in the environment.
- **GitHub App (PEM) mode**: Set `GITHUB_APP_ID` and `GITHUB_INSTALLATION_ID` (or store them in the secret as above). The script uses `npx obtain-github-app-installation-access-token` when Node/npx is available; otherwise it falls back to a built-in JWT + curl flow (requires `openssl`, `curl`).

## Usage

### SSH key (e.g. deploy key)

If the secret holds an **SSH private key**, the script sets `GIT_SSH_COMMAND` so git uses that key for `git@github.com`:

```bash
export GITHUB_KEY_SECRET_NAME=my-repo/deploy-key
./git-auth-with-secret.sh
# In the same shell, run git commands:
git clone git@github.com:org/repo.git
```

Or run a single command with auth configured:

```bash
GITHUB_KEY_SECRET_NAME=my-repo/deploy-key ./git-auth-with-secret.sh -- git clone git@github.com:org/repo.git
```

### GitHub App private key (PEM)

If the secret holds a **GitHub App private key** (PEM), you must set the App ID and Installation ID. The script obtains an installation access token (via `npx obtain-github-app-installation-access-token` when available) and configures git for HTTPS:

```bash
export GITHUB_KEY_SECRET_NAME=github-ai-bot   # or your secret name
export GITHUB_APP_ID=123456                   # your GitHub App ID
export GITHUB_INSTALLATION_ID=789             # installation ID for the org/repo
./git-auth-with-secret.sh
# Then use HTTPS (token is in GH_TOKEN and used via credential helper):
git clone https://github.com/org/repo.git
```

If you run the script with only the secret name and the secret is a PEM, it will error and remind you to set `GITHUB_APP_ID` and `GITHUB_INSTALLATION_ID`.

Or run a command directly:

```bash
GITHUB_KEY_SECRET_NAME=my-app/private-key \
GITHUB_APP_ID=123456 \
GITHUB_INSTALLATION_ID=789 \
./git-auth-with-secret.sh -- git pull
```

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_KEY_SECRET_NAME` | Yes | Secrets Manager secret ID or name |
| `GITHUB_APP_ID` | For App PEM | GitHub App ID (numeric) |
| `GITHUB_INSTALLATION_ID` | For App PEM | GitHub App installation ID |
| `AWS_REGION` | No | AWS region (default from config) |
| `AWS_PROFILE` | No | AWS CLI profile |

## Keeping credentials refreshed (GitHub App only)

GitHub App installation tokens expire (typically after 1 hour). For a machine where you want git to always have a valid token without running `git-auth-with-secret.sh` in each shell, use the **credential refresh daemon**:

- **`git-credential-refresh-daemon.sh`** — Runs in a loop: fetches a new token from AWS Secrets Manager and the GitHub API, writes it to a file, and configures git's credential helper to use that file. Any `git` command (in any shell) then uses the current token.
- **`git-credential-helper.sh`** — Used by git as `credential.https://github.com.helper`; reads the token from the file the daemon updates.

Same environment variables as above (GitHub App mode). Optional:

| Variable | Default | Description |
|----------|---------|-------------|
| `GIT_AUTH_TOKEN_FILE` | `~/.config/git-auth/github-token` | Where the daemon writes the token (and the helper reads it). |
| `GIT_AUTH_REFRESH_INTERVAL_SECONDS` | `1800` (30 min) | How often to refresh the token. |

### Run the daemon manually

```bash
export GITHUB_KEY_SECRET_NAME=my-app/private-key
export GITHUB_APP_ID=123456
export GITHUB_INSTALLATION_ID=789
./git-credential-refresh-daemon.sh
# Runs until Ctrl+C. Use screen/tmux if you want it in the background.
```

### Run on startup (systemd)

1. Copy and edit the unit file:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ami/git/git-credential-refresh.service ~/.config/systemd/user/
   # Edit and set GITHUB_KEY_SECRET_NAME, GITHUB_APP_ID, GITHUB_INSTALLATION_ID,
   # and the ExecStart path to your ami/git directory.
   ```
2. Enable and start:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now git-credential-refresh.service
   ```

The daemon installs the credential helper globally (`git config --global credential.https://github.com.helper`), so once it's running, any `git clone` / `git pull` over HTTPS to GitHub uses the refreshed token. SSH deploy keys do not expire; the one-shot `git-auth-with-secret.sh` is enough for those.

## Security

- The key is written to a temporary file with mode `600` and removed when the script exits (SSH key path). For GitHub App, the installation token is kept only in the process environment (`GH_TOKEN`).
- The generated `git-askpass-helper.sh` (GitHub App mode) reads the token from `GH_TOKEN` and is not written to disk; the copy in `ami/git/` is a stub and is gitignored.
- The refresh daemon writes the installation token to `GIT_AUTH_TOKEN_FILE` (default `~/.config/git-auth/github-token`) with mode `600`; only the credential helper (invoked by git) reads it.
