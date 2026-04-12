#!/usr/bin/env bash
# git credential helper: routes https://github.com/<org>/... to the token file
# for that org (see bots.json + set-git-credentials-from-secret.sh).
# Installed as: git config --global credential.https://github.com.helper "!$0"
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=git-auth-common.sh
source "$SCRIPT_DIR/git-auth-common.sh"

op="${1:-}"
[[ "$op" == "get" ]] || exit 0

CONFIG="$(git_auth_default_config_path)"
[[ -f "$CONFIG" ]] || exit 0
command -v jq &>/dev/null || exit 0

protocol="" host="" path=""
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" ]] && break
  case "$line" in
    protocol=*) protocol="${line#protocol=}" ;;
    host=*) host="${line#host=}" ;;
    path=*) path="${line#path=}" ;;
  esac
done

[[ "$protocol" == "https" && "$host" == "github.com" ]] || exit 0
path="${path#/}"
[[ -n "$path" ]] || exit 0
org_from_url="${path%%/*}"
[[ -n "$org_from_url" ]] || exit 0

matched_org="$(
  jq -r --arg u "$org_from_url" '
    [.bots[]? | select((.org | ascii_downcase) == ($u | ascii_downcase)) | .org] | first // empty
  ' "$CONFIG"
)"
[[ -n "$matched_org" ]] || exit 0

TOKEN_FILE="$(git_auth_token_file_for_org "$matched_org")"
[[ -r "$TOKEN_FILE" ]] || exit 0
token="$(cat "$TOKEN_FILE")"
[[ -n "$token" ]] || exit 0

echo "username=x-access-token"
echo "password=${token}"
