#!/usr/bin/env bash
# Run on the test instance (booted from the registered AMI) by `desk ami build` test phase.
set -euo pipefail

# Home should contain only dotfiles/dotdirs and the desk repo — no other top-level entries.
extra="$(find /home/ubuntu -maxdepth 1 -mindepth 1 ! -name '.*' ! -name desk 2>/dev/null || true)"
if [[ -n "${extra}" ]]; then
  echo "Unexpected top-level entries in /home/ubuntu (want only hidden paths and desk/):" >&2
  echo "${extra}" >&2
  exit 1
fi
