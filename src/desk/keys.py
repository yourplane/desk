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
