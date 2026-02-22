#!/usr/bin/env bash
# Install AWS CLI v2 on Ubuntu (x86_64).
# Safe to run multiple times (idempotent).
set -e

if command -v aws &>/dev/null && aws --version 2>/dev/null | grep -q "aws-cli/2"; then
  echo "AWS CLI v2 already installed: $(aws --version)"
  exit 0
fi

echo "Installing dependencies (curl, unzip)..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl unzip

echo "Installing AWS CLI v2..."
cd /tmp
curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q -o awscliv2.zip
sudo ./aws/install -i /usr/local/aws-cli -b /usr/local/bin
rm -rf awscliv2.zip aws
echo "Done: $(aws --version)"
