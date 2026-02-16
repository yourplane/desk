#!/usr/bin/env bash
# Bundle desk package into reaper and run sam build. Run from repo root or infrastructure/.
set -e
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
REAPER_DIR="$SCRIPT_DIR/reaper"
DESK_SRC="$REPO_ROOT/src/desk"
DESK_DEST="$REAPER_DIR/desk"

if [ ! -d "$DESK_SRC" ]; then
  echo "Error: $DESK_SRC not found. Run from repo root or infrastructure/." >&2
  exit 1
fi

rm -rf "$DESK_DEST"
cp -r "$DESK_SRC" "$DESK_DEST"
cd "$SCRIPT_DIR"
sam build --template desk-reaper.yaml "$@"
