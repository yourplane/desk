# Git auth with Secrets Manager

`git-auth-with-secret.sh` fetches a GitHub private key from AWS Secrets Manager and configures the environment so `git` can authenticate with GitHub.

## Requirements

- **AWS CLI** configured (credentials and region) with permission to `secretsmanager:GetSecretValue`
- **Secret**: Stored in Secrets Manager; value can be:
  - Raw PEM or SSH private key string, or
  - JSON with a `private_key` or `key` field (e.g. `{"private_key":"-----BEGIN RSA PRIVATE KEY-----\n..."}`). For JSON, `jq` is recommended.
- **GitHub App (PEM) mode**: Set `GITHUB_APP_ID` and `GITHUB_INSTALLATION_ID`. The script uses `npx obtain-github-app-installation-access-token` when Node/npx is available; otherwise it falls back to a built-in JWT + curl flow (requires `openssl`, `curl`).

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

## Security

- The key is written to a temporary file with mode `600` and removed when the script exits (SSH key path). For GitHub App, the installation token is kept only in the process environment (`GH_TOKEN`).
- The generated `git-askpass-helper.sh` (GitHub App mode) reads the token from `GH_TOKEN` and is not written to disk; the copy in `ami/` is a stub and is gitignored.
