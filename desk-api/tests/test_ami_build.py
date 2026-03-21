"""AMI build API routes (config guard)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.routes.ami_build as ami_build_mod
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_ami_recipes_unconfigured_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ami_build_mod, "RECIPES_TABLE", "")
    monkeypatch.setattr(ami_build_mod, "BUILDS_TABLE", "")
    monkeypatch.setattr(ami_build_mod, "SFN_ARN", "")
    r = client.get("/api/ami-recipes")
    assert r.status_code == 503


def test_ami_builds_unconfigured_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ami_build_mod, "RECIPES_TABLE", "")
    monkeypatch.setattr(ami_build_mod, "BUILDS_TABLE", "")
    monkeypatch.setattr(ami_build_mod, "SFN_ARN", "")
    r = client.get("/api/ami-builds")
    assert r.status_code == 503
