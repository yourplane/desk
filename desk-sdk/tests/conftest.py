"""Pytest configuration: isolate tests from user desk config."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _clear_aws_client_caches() -> None:
    """Prevent lru_cache on boto clients/sessions from leaking mocks between tests."""
    from desk import aws as aws_mod

    aws_mod._ec2_client_cached.cache_clear()
    aws_mod._get_desk_vpc_outputs_impl.cache_clear()
    yield
    aws_mod._ec2_client_cached.cache_clear()
    aws_mod._get_desk_vpc_outputs_impl.cache_clear()


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
