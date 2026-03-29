"""Pytest configuration: isolate tests from user desk config."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _reset_cli_desk_profile() -> None:
    """Clear ``--desk-profile`` override between CLI tests."""
    from desk.config import reset_cli_desk_profile_override

    reset_cli_desk_profile_override()
    yield
    reset_cli_desk_profile_override()


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
