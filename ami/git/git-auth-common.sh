#!/usr/bin/env bash
# Shared helpers for git-auth multi-bot scripts (sourced, not executed).
GIT_AUTH_DIR="${HOME}/.config/git-auth"
GIT_AUTH_TOKENS_DIR="${GIT_AUTH_DIR}/tokens"

git_auth_default_config_path() {
  if [[ -n "${GITHUB_KEY_SECRET_CONFIG:-}" ]]; then
    echo "$GITHUB_KEY_SECRET_CONFIG"
    return
  fi
  local p="${GIT_AUTH_DIR}/config.path"
  if [[ -f "$p" ]]; then
    IFS= read -r line <"$p" || true
    [[ -n "${line:-}" ]] && echo "$line" && return
  fi
  echo "${GIT_AUTH_DIR}/bots.json"
}

# GitHub org names allow [A-Za-z0-9-]; normalize for a safe filename.
git_auth_sanitize_org() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/_/g'
}

git_auth_token_file_for_org() {
  echo "${GIT_AUTH_TOKENS_DIR}/$(git_auth_sanitize_org "$1")"
}
