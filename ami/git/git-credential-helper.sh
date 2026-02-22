#!/usr/bin/env bash
#
# Git credential helper that supplies username and password from the token file
# written by git-credential-refresh-daemon.sh. Use this with credential.https://github.com.helper
# so any git process (not just the daemon's shell) can use the refreshed token.
#
# Usage: git config --global credential.https://github.com.helper '!/path/to/git-credential-helper.sh'
#
# The token file path is read from the same default as the daemon:
#   GIT_AUTH_TOKEN_FILE or ~/.config/git-auth/github-token
#
set -e

TOKEN_FILE="${GIT_AUTH_TOKEN_FILE:-${HOME}/.config/git-auth/github-token}"

if [[ "$1" != "get" ]]; then
  # store/erase: no-op (we don't persist credentials ourselves)
  exit 0
fi

# Consume stdin (git sends protocol=https, host=github.com, etc.)
while IFS= read -r line; do
  : "$line"
done

if [[ ! -r "$TOKEN_FILE" ]]; then
  exit 1
fi

echo "username=x-access-token"
echo "password=$(cat "$TOKEN_FILE")"
