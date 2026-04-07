#!/usr/bin/env bash
# Run on the test instance (booted from the registered AMI) by `desk ami build` test phase.
set -euo pipefail
export PATH="/home/ubuntu/desk/.venv/bin:/home/ubuntu/desk:/opt/desk-uv-venv/bin:${PATH:-}"

# desk needs a region; config.ini should set [default].region — also resolve from IMDSv2 if unset.
if [[ -z "${AWS_REGION:-}" && -z "${AWS_DEFAULT_REGION:-}" ]]; then
  _tok="$(curl -s -S -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || true)"
  if [[ -n "${_tok}" ]]; then
    AWS_REGION="$(curl -s -S -m 2 -H "X-aws-ec2-metadata-token: ${_tok}" \
      http://169.254.169.254/latest/meta-data/placement/region)"
    export AWS_REGION
    export AWS_DEFAULT_REGION="${AWS_REGION}"
  fi
fi

desk list

# Home should contain only dotfiles/dotdirs and the desk repo — no other top-level entries.
extra="$(find /home/ubuntu -maxdepth 1 -mindepth 1 ! -name '.*' ! -name desk 2>/dev/null || true)"
if [[ -n "${extra}" ]]; then
  echo "Unexpected top-level entries in /home/ubuntu (want only hidden paths and desk/):" >&2
  echo "${extra}" >&2
  exit 1
fi
