"""Load desk config file for default region and profile."""

from __future__ import annotations

import os
from configparser import ConfigParser


def _get_config_path() -> str:
    """Path to desk config file (e.g. ~/.config/desk/config)."""
    if path := os.environ.get("DESK_CONFIG"):
        return path
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "desk", "config")


def _load_config() -> ConfigParser:
    """Load config from file. Returns empty parser if file missing or invalid."""
    parser = ConfigParser()
    path = _get_config_path()
    if os.path.isfile(path):
        try:
            parser.read(path)
        except Exception:
            pass
    return parser


def get_default_region() -> str | None:
    """Default AWS region: env (AWS_REGION, AWS_DEFAULT_REGION) then config file."""
    value = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if value:
        return value
    config = _load_config()
    if config.has_section("defaults") and config.has_option("defaults", "region"):
        return config.get("defaults", "region").strip() or None
    return None


def get_default_profile() -> str | None:
    """Default AWS profile: env (AWS_PROFILE) then config file."""
    value = os.environ.get("AWS_PROFILE")
    if value:
        return value
    config = _load_config()
    if config.has_section("defaults") and config.has_option("defaults", "profile"):
        return config.get("defaults", "profile").strip() or None
    return None
