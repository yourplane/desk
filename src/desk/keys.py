"""SSH key helpers: default key discovery and public key content."""

from __future__ import annotations

import os
import subprocess


def get_default_private_key_path() -> str | None:
    """Return path to the first existing default SSH private key (~/.ssh/id_ed25519 or id_rsa)."""
    ssh_dir = os.path.expanduser("~/.ssh")
    for name in ("id_ed25519", "id_rsa"):
        path = os.path.join(ssh_dir, name)
        if os.path.isfile(path):
            return path
    return None


def get_public_key_content(private_key_path: str) -> str:
    """Return the public key (one line) for a private key file.

    Tries the corresponding .pub file first (e.g. key.pem -> key.pub).
    If not found, runs ssh-keygen -y -f private_key_path to derive it.
    """
    if not os.path.isfile(private_key_path):
        raise FileNotFoundError(f"Private key not found: {private_key_path}")
    # Standard .pub location
    if private_key_path.endswith(".pem"):
        pub_path = private_key_path[:-4] + ".pub"
    else:
        pub_path = private_key_path + ".pub"
    if os.path.isfile(pub_path):
        with open(pub_path) as f:
            line = f.read().strip()
        if line and not line.startswith("-----"):
            return line
    # Derive from private key
    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", private_key_path],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not derive public key from {private_key_path}: {result.stderr or result.stdout}"
        )
    return result.stdout.strip()
