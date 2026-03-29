"""Load desk config file for default region and profile."""

from __future__ import annotations

import os
import re
from configparser import ConfigParser
from dataclasses import dataclass

_DESK_PROFILE_OVERRIDE_EXPLICIT = False
_DESK_PROFILE_OVERRIDE_VALUE: str | None = None

_PROFILE_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


@dataclass(frozen=True)
class AwsSettings:
    """Resolved AWS API defaults (region and credential profile name for boto3)."""

    region: str | None
    profile: str | None


@dataclass(frozen=True)
class DeskSettings:
    """Resolved desk configuration: active desk profile name, AWS defaults, AMI prefix."""

    active_desk_profile_name: str | None
    aws_settings: AwsSettings
    ami_prefix: str | None


def set_desk_profile_override(value: str | None) -> None:
    """Set an explicit desk profile name (overrides ``DESK_PROFILE`` and config).

    Used by the CLI when the user passes the global ``--profile`` before the subcommand.
    """
    global _DESK_PROFILE_OVERRIDE_EXPLICIT, _DESK_PROFILE_OVERRIDE_VALUE
    _DESK_PROFILE_OVERRIDE_EXPLICIT = True
    _DESK_PROFILE_OVERRIDE_VALUE = value


def reset_desk_profile_override() -> None:
    """Clear explicit desk profile override (for tests)."""
    global _DESK_PROFILE_OVERRIDE_EXPLICIT, _DESK_PROFILE_OVERRIDE_VALUE
    _DESK_PROFILE_OVERRIDE_EXPLICIT = False
    _DESK_PROFILE_OVERRIDE_VALUE = None


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


def _desk_profile_from_config_file(config: ConfigParser) -> str | None:
    """``desk_profile`` from ``[default]``."""
    if config.has_section("default") and config.has_option("default", "desk_profile"):
        s = config.get("default", "desk_profile").strip()
        return s or None
    return None


def _resolve_active_desk_profile_name(config: ConfigParser) -> str | None:
    """Active desk profile: explicit override, then ``DESK_PROFILE``, then ``[default] desk_profile``."""
    global _DESK_PROFILE_OVERRIDE_EXPLICIT, _DESK_PROFILE_OVERRIDE_VALUE
    if _DESK_PROFILE_OVERRIDE_EXPLICIT:
        if _DESK_PROFILE_OVERRIDE_VALUE is None:
            return None
        s = _DESK_PROFILE_OVERRIDE_VALUE.strip()
        return s or None
    value = os.environ.get("DESK_PROFILE")
    if value:
        s = value.strip()
        if s:
            return s
    return _desk_profile_from_config_file(config)


def _defaults_ini_section(config: ConfigParser, active_desk_profile: str | None) -> str | None:
    """INI section for ``region`` / ``aws_profile`` / ``ami_prefix``: named profile or ``[default]``."""
    if active_desk_profile:
        sec = desk_profile_section(active_desk_profile)
        if config.has_section(sec):
            return sec
    if config.has_section("default"):
        return "default"
    return None


def get_desk_settings() -> DeskSettings:
    """Load config once and return resolved desk and AWS defaults."""
    config = _load_config()
    active = _resolve_active_desk_profile_name(config)
    sec = _defaults_ini_section(config, active)

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region and sec and config.has_option(sec, "region"):
        region = config.get(sec, "region").strip() or None

    profile = os.environ.get("AWS_PROFILE")
    if not profile and sec:
        profile = _aws_profile_from_section(config, sec)

    ami_env = os.environ.get("DESK_AMI_PREFIX")
    if ami_env:
        ami_prefix = ami_env.strip() or None
    else:
        ami_prefix = None
        if sec and config.has_option(sec, "ami_prefix"):
            ami_prefix = config.get(sec, "ami_prefix").strip() or None

    return DeskSettings(
        active_desk_profile_name=active,
        aws_settings=AwsSettings(region=region, profile=profile),
        ami_prefix=ami_prefix,
    )


def _aws_profile_from_section(config: ConfigParser, sec: str) -> str | None:
    """AWS credential profile from ``aws_profile``."""
    if config.has_option(sec, "aws_profile"):
        return config.get(sec, "aws_profile").strip() or None
    return None


def get_active_desk_profile_name() -> str | None:
    """Active desk profile: explicit override, then ``DESK_PROFILE``, then config ``desk_profile``."""
    return get_desk_settings().active_desk_profile_name


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
    return get_desk_settings().aws_settings.region


def get_default_profile() -> str | None:
    """Default AWS profile: env (AWS_PROFILE) then config file ``aws_profile``."""
    return get_desk_settings().aws_settings.profile


def get_default_ami_prefix() -> str | None:
    """Default AMI name prefix: env (DESK_AMI_PREFIX) then config file. Used when creating a workstation without --ami."""
    return get_desk_settings().ami_prefix
