"""Tests for desk config (default region and profile)."""

from __future__ import annotations

import os
import tempfile

import pytest

from desk.config import get_default_profile, get_default_region, _get_config_path


def test_get_default_region_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment AWS_REGION overrides config file."""
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nregion = us-east-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_region() == "us-west-2"
    finally:
        os.unlink(path)


def test_get_default_region_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file region used when env is not set."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nregion = eu-west-1\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_region() == "eu-west-1"
    finally:
        os.unlink(path)


def test_get_default_region_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No region when env and config are absent."""
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nprofile = only-profile\n")  # no region
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_region() is None
    finally:
        os.unlink(path)


def test_get_default_profile_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment AWS_PROFILE overrides config file."""
    monkeypatch.setenv("AWS_PROFILE", "env-profile")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nprofile = config-profile\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_profile() == "env-profile"
    finally:
        os.unlink(path)


def test_get_default_profile_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config file profile used when env is not set."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nprofile = my-aws-profile\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_profile() == "my-aws-profile"
    finally:
        os.unlink(path)


def test_get_default_profile_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No profile when env and config are absent."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("[defaults]\nregion = us-east-1\n")  # no profile
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        assert get_default_profile() is None
    finally:
        os.unlink(path)


def test_config_path_uses_desk_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DESK_CONFIG env var sets config path."""
    monkeypatch.setenv("DESK_CONFIG", "/custom/desk/config")
    assert _get_config_path() == "/custom/desk/config"


def test_config_path_default_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path is XDG_CONFIG_HOME/desk/config or ~/.config/desk/config."""
    monkeypatch.delenv("DESK_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg/config")
    assert _get_config_path() == "/xdg/config/desk/config"
