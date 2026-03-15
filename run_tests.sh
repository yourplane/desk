#!/usr/bin/env bash
# Run desk-sdk and desk-cli tests. Use from repo root: ./run_tests.sh
# Running both in one pytest invocation causes ImportPathMismatchError (duplicate tests/conftest).
set -e
cd "$(cd "$(dirname "$0")" && pwd)"
uv run pytest desk-sdk/tests -q "$@" || exit $?
uv run pytest desk-cli/tests -q "$@"
