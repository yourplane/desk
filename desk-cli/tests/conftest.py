"""Pytest configuration: isolate tests from user desk config."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _block_host_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid using the machine's real AWS identity during tests (e.g. EC2 instance role).

    Without this, a missed mock on boto3 can create real resources when tests run on EC2.
    """
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)


@pytest.fixture(autouse=True)
def _reset_desk_profile_override() -> None:
    """Clear root ``--profile`` (desk profile) override between CLI tests."""
    from desk.config import reset_desk_profile_override

    reset_desk_profile_override()
    yield
    reset_desk_profile_override()


@pytest.fixture(autouse=True)
def _isolate_desk_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DESK_CONFIG at an empty file so tests don't use the user's config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
        f.write("; empty config for tests\n")
        path = f.name
    try:
        monkeypatch.setenv("DESK_CONFIG", path)
        yield
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _default_ssm_forward_listening() -> None:
    """Treat mocked routes as healthy unless a test overrides forward probing."""
    with patch("desk_cli.commands.route._local_forward_listening", return_value=True):
        yield
