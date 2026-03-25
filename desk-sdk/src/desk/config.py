"""Load desk config file for default region and profile."""

from __future__ import annotations

import os
from configparser import ConfigParser


def _get_config_path() -> str:
    """Path to desk config file (e.g. ~/.config/desk/config.ini)."""
    if path := os.environ.get("DESK_CONFIG"):
        return path
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "desk", "config.ini")


def get_state_home() -> str:
    """Base directory for desk-managed state (e.g. routes, logs).

    Resolution order:
    - ``DESK_STATE_HOME`` if set
    - ``XDG_STATE_HOME/desk`` if ``XDG_STATE_HOME`` is set
    - ``~/.local/state/desk`` otherwise
    """
    if value := os.environ.get("DESK_STATE_HOME"):
        return value
    if value := os.environ.get("XDG_STATE_HOME"):
        return os.path.join(value, "desk")
    return os.path.expanduser("~/.local/state/desk")


def _load_config() -> ConfigParser:
    """Load config from file. Returns empty parser if file missing or invalid."""
    parser = ConfigParser()
    path = _get_config_path()
    if os.path.isfile(path):
        try:
            parser.read(path, encoding="utf-8")
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


def get_default_ami_prefix() -> str | None:
    """Default AMI name prefix: env (DESK_AMI_PREFIX) then config file. Used when creating a workstation without --ami."""
    value = os.environ.get("DESK_AMI_PREFIX")
    if value:
        return value.strip() or None
    config = _load_config()
    if config.has_section("defaults") and config.has_option("defaults", "ami_prefix"):
        return config.get("defaults", "ami_prefix").strip() or None
    return None
