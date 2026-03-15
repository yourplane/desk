"""Pytest configuration: isolate tests from user desk config."""

from __future__ import annotations

import os
import tempfile

import pytest


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
