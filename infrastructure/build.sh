#!/usr/bin/env bash
# Bundle desk package into reaper and control Lambdas and run sam build. Run from repo root or infrastructure/.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DESK_SRC="$REPO_ROOT/src/desk"

if [ ! -d "$DESK_SRC" ]; then
  echo "Error: $DESK_SRC not found. Run from repo root or infrastructure/." >&2
  exit 1
fi

# Reaper
REAPER_DIR="$SCRIPT_DIR/reaper"
rm -rf "$REAPER_DIR/desk"
cp -r "$DESK_SRC" "$REAPER_DIR/desk"

# Control plane Lambda
CONTROL_DIR="$SCRIPT_DIR/control"
rm -rf "$CONTROL_DIR/desk"
cp -r "$DESK_SRC" "$CONTROL_DIR/desk"

cd "$SCRIPT_DIR"
sam build --template desk-reaper.yaml "$@"
sam build --template desk-control.yaml "$@"
