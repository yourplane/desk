#!/usr/bin/env bash
# Run desk-sdk and desk-cli tests. Use from repo root: ./run_tests.sh
# Running both in one pytest invocation causes ImportPathMismatchError (duplicate tests/conftest).
# Use this repo's .venv only (unset VIRTUAL_ENV/PYTHONPATH and run via .venv/bin/python) so
# the workspace desk package is used, not another env's (e.g. another project's .tox).
set -e
cd "$(cd "$(dirname "$0")" && pwd)"
unset VIRTUAL_ENV
unset PYTHONPATH
uv sync -q --extra dev
.venv/bin/python -m pytest desk-sdk/tests -q "$@" || exit $?
.venv/bin/python -m pytest desk-cli/tests -q "$@" || exit $?
.venv/bin/python -m pytest desk-api/tests -q "$@"
