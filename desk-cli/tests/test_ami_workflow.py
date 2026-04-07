"""Unit tests for async AMI workflow planning (no AWS)."""

from desk_cli.ami_build.recipe_eval import AsyncRecipeEval
from desk_cli.ami_build.snapshot import AsyncAmiBuildSnapshot
from desk_cli.ami_build.step_engine import AsyncWorkflowStepKind, plan_async_build_step


def _snap(**kwargs: object) -> AsyncAmiBuildSnapshot:
    base = dict(
        build_id="b1",
        bucket="bk",
        s3_prefix="ami-builds/b1/",
        config={"ami_name": "n", "steps": [{"run": "echo hi"}]},
        recorded_instance_id=None,
        ec2_state=None,
        ec2_missing=False,
        ssm_ready=None,
        ami_result=None,
        registered_ami_id=None,
        registered_ami_state=None,
        async_pipeline_fully_complete=False,
    )
    base.update(kwargs)
    return AsyncAmiBuildSnapshot(**base)  # type: ignore[arg-type]


def test_plan_no_builder_creates_builder() -> None:
    snap = _snap()
    kind, _ = plan_async_build_step(snap, None, retry=False, no_wait=False)
    assert kind == AsyncWorkflowStepKind.CREATE_BUILDER


def test_plan_ssm_ready_next_recipe_step() -> None:
    snap = _snap(
        recorded_instance_id="i-1",
        ec2_state="running",
        ec2_missing=False,
        ssm_ready=True,
    )
    ev = AsyncRecipeEval(
        total_steps=1,
        steps=({"run": "echo hi"},),
        blocked=False,
        blocked_step_index=None,
        blocked_command_id=None,
        last_error=None,
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=0,
        recipe_complete=False,
    )
    kind, payload = plan_async_build_step(snap, ev, retry=False, no_wait=False)
    assert kind == AsyncWorkflowStepKind.SEND_NEXT_RECIPE_SSM
    assert payload["step_index"] == 0
    assert payload["recipe_kind"] == "run"
