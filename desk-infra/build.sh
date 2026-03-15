#!/usr/bin/env bash
# Build reaper and control Lambdas (desk-sdk installed via uv). Run from repo root or desk-infra/.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

if [ ! -d "$REPO_ROOT/desk-sdk" ]; then
  echo "Error: desk-sdk not found at $REPO_ROOT/desk-sdk. Run from repo root or desk-infra/." >&2
  exit 1
fi

cd "$SCRIPT_DIR"
# Use --build-in-source so the Makefile runs in the real repo and can find ../../desk-sdk.
sam build --build-in-source --template desk-reaper.yaml "$@"
sam build --build-in-source --template desk-control.yaml "$@"
