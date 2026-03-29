"""Load desk config file for default region and profile."""

from __future__ import annotations

import os
import re
from configparser import ConfigParser

_CLI_DESK_PROFILE_EXPLICIT = False
_CLI_DESK_PROFILE_VALUE: str | None = None

_PROFILE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def set_cli_desk_profile_override(value: str | None) -> None:
    """Set desk profile from CLI ``--desk-profile`` (takes precedence over env and config)."""
    global _CLI_DESK_PROFILE_EXPLICIT, _CLI_DESK_PROFILE_VALUE
    _CLI_DESK_PROFILE_EXPLICIT = True
    _CLI_DESK_PROFILE_VALUE = value


def reset_cli_desk_profile_override() -> None:
    """Clear CLI desk profile override (for tests)."""
    global _CLI_DESK_PROFILE_EXPLICIT, _CLI_DESK_PROFILE_VALUE
    _CLI_DESK_PROFILE_EXPLICIT = False
    _CLI_DESK_PROFILE_VALUE = None


def _get_config_path() -> str:
    """Path to desk config file (e.g. ~/.config/desk/config.ini)."""
    if path := os.environ.get("DESK_CONFIG"):
        return path
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "desk", "config.ini")


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


def desk_profile_section(desk_profile_name: str) -> str:
    """INI section name for a desk profile (e.g. ``profile work`` → section ``[profile work]``)."""
    return f"profile {desk_profile_name.strip()}"


def get_active_desk_profile_name() -> str | None:
    """Active desk profile: CLI ``--desk-profile``, then ``DESK_PROFILE``, then ``[defaults] desk_profile``."""
    global _CLI_DESK_PROFILE_EXPLICIT, _CLI_DESK_PROFILE_VALUE
    if _CLI_DESK_PROFILE_EXPLICIT:
        if _CLI_DESK_PROFILE_VALUE is None:
            return None
        s = _CLI_DESK_PROFILE_VALUE.strip()
        return s or None
    value = os.environ.get("DESK_PROFILE")
    if value:
        s = value.strip()
        if s:
            return s
    config = _load_config()
    if config.has_section("defaults") and config.has_option("defaults", "desk_profile"):
        s = config.get("defaults", "desk_profile").strip()
        return s or None
    return None


def _sanitize_profile_segment(name: str) -> str:
    """Safe single path segment for state directory names."""
    s = name.strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        raise ValueError(f"Invalid desk profile name: {name!r}")
    if not _PROFILE_SEGMENT_RE.match(s):
        raise ValueError(
            f"Invalid desk profile name {name!r}: use letters, digits, ._- only, "
            "and do not start with punctuation other than a letter or digit."
        )
    return s


def _file_defaults_section(parser: ConfigParser) -> str | None:
    """Section to read ``region`` / ``profile`` / ``ami_prefix`` from (named profile or ``defaults``)."""
    active = get_active_desk_profile_name()
    if active:
        sec = desk_profile_section(active)
        if parser.has_section(sec):
            return sec
    if parser.has_section("defaults"):
        return "defaults"
    return None


def get_state_home() -> str:
    """Base directory for desk-managed state (e.g. routes, logs).

    When a desk profile is active, state is under a subdirectory named after that
    profile to avoid collisions between AWS accounts.

    Resolution order:
    - ``DESK_STATE_HOME`` if set (still namespaced by desk profile when active)
    - ``XDG_STATE_HOME/desk`` if ``XDG_STATE_HOME`` is set
    - ``~/.local/state/desk`` otherwise
    """
    if value := os.environ.get("DESK_STATE_HOME"):
        base = value
    elif value := os.environ.get("XDG_STATE_HOME"):
        base = os.path.join(value, "desk")
    else:
        base = os.path.expanduser("~/.local/state/desk")

    name = get_active_desk_profile_name()
    if not name:
        return base
    return os.path.join(base, _sanitize_profile_segment(name))


def get_default_region() -> str | None:
    """Default AWS region: env (AWS_REGION, AWS_DEFAULT_REGION) then config file."""
    value = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if value:
        return value
    config = _load_config()
    sec = _file_defaults_section(config)
    if sec and config.has_option(sec, "region"):
        return config.get(sec, "region").strip() or None
    return None


def get_default_profile() -> str | None:
    """Default AWS profile: env (AWS_PROFILE) then config file."""
    value = os.environ.get("AWS_PROFILE")
    if value:
        return value
    config = _load_config()
    sec = _file_defaults_section(config)
    if sec and config.has_option(sec, "profile"):
        return config.get(sec, "profile").strip() or None
    return None


def get_default_ami_prefix() -> str | None:
    """Default AMI name prefix: env (DESK_AMI_PREFIX) then config file. Used when creating a workstation without --ami."""
    value = os.environ.get("DESK_AMI_PREFIX")
    if value:
        return value.strip() or None
    config = _load_config()
    sec = _file_defaults_section(config)
    if sec and config.has_option(sec, "ami_prefix"):
        return config.get(sec, "ami_prefix").strip() or None
    return None
