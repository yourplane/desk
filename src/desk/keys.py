"""Key path helpers for desk-managed keys."""

from __future__ import annotations

import os


def get_desk_keys_dir() -> str:
    """Return the desk keys directory (~/.config/desk/keys/)."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "desk", "keys")


def get_key_path(name: str) -> str:
    """Return the path for a key file by name."""
    return os.path.join(get_desk_keys_dir(), f"{name}.pem")


def list_local_keys() -> set[str]:
    """Return set of key names that exist in the desk keys folder."""
    keys_dir = get_desk_keys_dir()
    if not os.path.isdir(keys_dir):
        return set()
    names: set[str] = set()
    for f in os.listdir(keys_dir):
        if f.endswith(".pem"):
            names.add(f[:-4])  # strip .pem
    return names
