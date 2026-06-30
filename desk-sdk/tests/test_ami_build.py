"""Tests for desk.ami_build."""

from __future__ import annotations

from unittest.mock import patch

from desk.ami_build import (
    AmiBuildSnapshot,
    RecipeEval,
    normalize_build_id,
    status_summary,
)


def test_normalize_build_id_strips_prefix() -> None:
    assert normalize_build_id("ami-builds/foo/") == "foo"
    assert normalize_build_id("ami-build-archive/bar") == "bar"


def test_status_summary_complete() -> None:
    snap = AmiBuildSnapshot(
        build_id="b1",
        bucket="b",
        s3_prefix="ami-builds/b1/",
        archived=False,
        config={"ami_name": "x"},
        manifest=None,
        recorded_instance_id=None,
        ec2_state=None,
        ec2_missing=False,
        ssm_ready=None,
        ami_result=None,
        registered_ami_id="ami-1",
        registered_ami_state="available",
        async_pipeline_fully_complete=True,
        test_recorded_instance_id=None,
        test_ec2_state=None,
        test_ec2_missing=False,
        test_ssm_ready=None,
    )
    s = status_summary(snap)
    assert s["phase"] == "complete"


def test_status_summary_pending_builder() -> None:
    snap = AmiBuildSnapshot(
        build_id="b1",
        bucket="b",
        s3_prefix="ami-builds/b1/",
        archived=False,
        config={"ami_name": "x"},
        manifest=None,
        recorded_instance_id=None,
        ec2_state=None,
        ec2_missing=False,
        ssm_ready=None,
        ami_result=None,
        registered_ami_id=None,
        registered_ami_state=None,
        async_pipeline_fully_complete=False,
        test_recorded_instance_id=None,
        test_ec2_state=None,
        test_ec2_missing=False,
        test_ssm_ready=None,
    )
    s = status_summary(snap)
    assert s["phase"] == "pending"


def test_status_summary_build_failed() -> None:
    snap = AmiBuildSnapshot(
        build_id="b1",
        bucket="b",
        s3_prefix="ami-builds/b1/",
        archived=False,
        config={"ami_name": "x", "build": [{"run": "echo"}]},
        manifest=None,
        recorded_instance_id="i-1",
        ec2_state="running",
        ec2_missing=False,
        ssm_ready=True,
        ami_result=None,
        registered_ami_id=None,
        registered_ami_state=None,
        async_pipeline_fully_complete=False,
        test_recorded_instance_id=None,
        test_ec2_state=None,
        test_ec2_missing=False,
        test_ssm_ready=None,
    )
    ev = RecipeEval(
        total_steps=1,
        steps=({"run": "echo"},),
        blocked=True,
        blocked_step_index=0,
        blocked_command_id="cmd-1",
        last_error="failed",
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=None,
        recipe_complete=False,
    )
    s = status_summary(snap, recipe_eval=ev)
    assert s["phase"] == "failed"
