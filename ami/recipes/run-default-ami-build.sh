#!/usr/bin/env bash
# Run full default AMI build + test from the desk repo root.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
exec uv run desk ami build run ami/recipes/default-desk-ami.json "$@"
