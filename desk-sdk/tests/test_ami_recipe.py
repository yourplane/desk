"""Tests for desk.ami_recipe validation."""

from __future__ import annotations

import pytest

from desk.ami_recipe import normalize_recipe_steps, validate_recipe_body


def test_normalize_steps_order() -> None:
    data = {
        "run_before_copy": ["echo a"],
        "copy": [{"source": "x", "dest": "y"}],
        "run": ["echo b"],
    }
    steps = normalize_recipe_steps(data)
    assert steps[0] == {"run": "echo a"}
    assert steps[1]["copy"]["source"] == "x"
    assert steps[2] == {"run": "echo b"}


def test_validate_cloud_requires_s3_copy() -> None:
    body = {
        "ami_name": "test",
        "instance_type": "t3.medium",
        "steps": [{"copy": {"source": "/tmp/x", "dest": "/tmp/y"}}],
    }
    with pytest.raises(ValueError, match="s3://"):
        validate_recipe_body(body, cloud=True)


def test_validate_cloud_accepts_s3() -> None:
    body = {
        "ami_name": "test",
        "instance_type": "t3.medium",
        "steps": [{"copy": {"source": "s3://my-bucket/prefix/file.sh", "dest": "/tmp/file.sh"}}],
    }
    out = validate_recipe_body(body, cloud=True)
    assert out["ami_name"] == "test"


def test_validate_requires_steps() -> None:
    body = {"ami_name": "x", "instance_type": "t3.medium", "steps": []}
    with pytest.raises(ValueError, match="at least one"):
        validate_recipe_body(body, cloud=True)
