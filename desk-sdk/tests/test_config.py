"""Tests for desk config (default region and profile)."""

from __future__ import annotations

import os
import tempfile

import pytest

from desk.config import (
    desk_profile_section,
    get_active_desk_profile_name,
    get_desk_settings,
    get_state_home,
    reset_desk_profile_override,
    set_desk_profile_override,
    _get_config_path,
)


def test_get_default_region_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment AWS_REGION overrides config file."""
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.region == "us-west-2"
    finally:
        os.unlink(path)


def test_get_default_region_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file region used when env is not set."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = eu-west-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.region == "eu-west-1"
    finally:
        os.unlink(path)


def test_get_default_region_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No region when env and config are absent."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\naws_profile = only-profile\n")  # no region
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.region is None
    finally:
        os.unlink(path)


def test_get_default_profile_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment AWS_PROFILE overrides config file."""
    monkeypatch.setenv("AWS_PROFILE", "env-profile")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\naws_profile = config-profile\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.profile == "env-profile"
    finally:
        os.unlink(path)


def test_get_default_profile_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file aws_profile used when env is not set."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\naws_profile = my-aws-profile\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.profile == "my-aws-profile"
    finally:
        os.unlink(path)


def test_get_default_profile_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No profile when env and config are absent."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-east-1\n")  # no profile
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.profile is None
    finally:
        os.unlink(path)


def test_config_path_uses_desk_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESK_CONFIG env var sets config path."""
    monkeypatch.setenv("DESK_CONFIG", "/custom/desk/config")
    assert _get_config_path() == "/custom/desk/config"


def test_config_path_default_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path is XDG_CONFIG_HOME/desk/config.ini or ~/.config/desk/config.ini."""
    monkeypatch.delenv("DESK_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/config")
    assert _get_config_path() == "/xdg/config/desk/config.ini"


def test_get_state_home_desk_state_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESK_STATE_HOME overrides XDG and default."""
    monkeypatch.setenv("DESK_STATE_HOME", "/custom/desk/state")
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert get_state_home() == "/custom/desk/state"


def test_get_state_home_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """XDG_STATE_HOME/desk when DESK_STATE_HOME unset."""
    monkeypatch.delenv("DESK_STATE_HOME", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert get_state_home() == "/xdg/state/desk"


def test_get_state_home_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default is ~/.local/state/desk."""
    monkeypatch.delenv("DESK_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/testuser")
    assert get_state_home() == "/home/testuser/.local/state/desk"


def test_get_default_ami_prefix_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESK_AMI_PREFIX env overrides config file."""
    monkeypatch.setenv("DESK_AMI_PREFIX", "my-desk-ami")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nami_prefix = config-prefix\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().ami_prefix == "my-desk-ami"
    finally:
        os.unlink(path)


def test_get_default_ami_prefix_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file ami_prefix used when env is not set."""
    monkeypatch.delenv("DESK_AMI_PREFIX", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nami_prefix = default-desk-ami\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().ami_prefix == "default-desk-ami"
    finally:
        os.unlink(path)


def test_get_default_ami_prefix_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ami_prefix when env and config are absent."""
    monkeypatch.delenv("DESK_AMI_PREFIX", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().ami_prefix is None
    finally:
        os.unlink(path)


def test_get_desk_settings_resolves_default_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_desk_settings() reads region, aws profile, and ami_prefix from [default]."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("DESK_AMI_PREFIX", raising=False)
    reset_desk_profile_override()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = ap-south-1\naws_profile = cfg-prof\nami_prefix = pfx\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        s = get_desk_settings()
        assert s.aws_settings.region == "ap-south-1"
        assert s.aws_settings.profile == "cfg-prof"
        assert s.ami_prefix == "pfx"
        assert s.active_desk_profile_name is None
        assert get_active_desk_profile_name() is None
    finally:
        os.unlink(path)


def test_desk_profile_section() -> None:
    """Section label matches INI ``[profile NAME]``."""
    assert desk_profile_section("work") == "profile work"


def test_get_active_desk_profile_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESK_PROFILE selects active desk profile."""
    reset_desk_profile_override()
    monkeypatch.setenv("DESK_PROFILE", "work")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_active_desk_profile_name() == "work"
    finally:
        os.unlink(path)


def test_desk_profile_in_default_section_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """[default] is the default profile; a desk_profile key there is not used."""
    reset_desk_profile_override()
    monkeypatch.delenv("DESK_PROFILE", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\ndesk_profile = staging\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_active_desk_profile_name() is None
    finally:
        os.unlink(path)


def test_get_active_desk_profile_explicit_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit override beats DESK_PROFILE."""
    monkeypatch.setenv("DESK_PROFILE", "env-profile")
    set_desk_profile_override("cli-profile")
    try:
        assert get_active_desk_profile_name() == "cli-profile"
    finally:
        reset_desk_profile_override()


def test_get_default_region_from_named_profile_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named [profile NAME] supplies region when desk profile is active."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    reset_desk_profile_override()
    monkeypatch.setenv("DESK_PROFILE", "work")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(
            "[default]\nregion = us-east-1\n"
            "[profile work]\n"
            "region = eu-west-1\n"
        )
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.region == "eu-west-1"
    finally:
        os.unlink(path)


def test_get_default_profile_from_named_profile_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named [profile NAME] supplies AWS profile when desk profile is active."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    reset_desk_profile_override()
    monkeypatch.setenv("DESK_PROFILE", "work")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write(
            "[default]\naws_profile = default-aws\n"
            "[profile work]\n"
            "aws_profile = work-aws\n"
        )
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.profile == "work-aws"
    finally:
        os.unlink(path)


def test_named_profile_missing_section_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """If [profile NAME] is missing, use [default] for file values."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    reset_desk_profile_override()
    monkeypatch.setenv("DESK_PROFILE", "missing")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-west-2\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_desk_settings().aws_settings.region == "us-west-2"
    finally:
        os.unlink(path)


def test_get_state_home_namespaced_when_desk_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active desk profile adds a subdirectory under state home."""
    monkeypatch.delenv("DESK_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/testuser")
    reset_desk_profile_override()
    monkeypatch.setenv("DESK_PROFILE", "work")
    assert get_state_home() == "/home/testuser/.local/state/desk/work"


def test_get_state_home_not_namespaced_without_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """No desk profile keeps flat state directory (backward compatible)."""
    monkeypatch.delenv("DESK_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("DESK_PROFILE", raising=False)
    monkeypatch.setenv("HOME", "/home/testuser")
    reset_desk_profile_override()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[default]\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_state_home() == "/home/testuser/.local/state/desk"
    finally:
        os.unlink(path)
