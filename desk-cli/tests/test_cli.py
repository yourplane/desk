"""CLI tests."""

import io
import json
import re
import subprocess
import sys
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from click.testing import CliRunner

from desk.config import AwsSettings, DeskSettings

from desk_cli.cli import cli

_TAB_CLI_SETTINGS = DeskSettings(
    active_desk_profile_name=None,
    aws_settings=AwsSettings("us-east-1", None),
    ami_prefix=None,
)


def _run_desk(*args: str) -> subprocess.CompletedProcess[str]:
    """Run desk CLI and return result. Click writes help to stderr."""
    return subprocess.run(
        [sys.executable, "-m", "desk_cli.cli", *args],
        capture_output=True,
        text=True,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    """Combine stdout and stderr (Click uses stderr for help)."""
    return result.stdout + result.stderr


def test_desk_up_help() -> None:
    """desk up --help succeeds."""
    result = _run_desk("up", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a workstation and connect to it" in output
    assert "WORKSTATION" in output
    assert "--forward" in output or "-L" in output


@patch("desk_cli.commands.up.get_default_private_key_path", return_value="/some/key")
@patch("desk_cli.commands.up.tab.tab_up")
@patch("desk_cli.commands.up.start_workstation")
@patch("desk_cli.commands.up.list_workstations")
@patch("desk_cli.commands.up.resolve_workstation")
def test_desk_up_starts_stopped_instance(
    mock_resolve: object,
    mock_list: object,
    mock_start: object,
    mock_tab_up: object,
    _mock_key: object,
) -> None:
    """desk up starts a stopped instance instead of creating."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="main", state="stopped"),
    ]
    mock_start.return_value = ("i-stopped", "2026-02-07T20:00:00Z")

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "main"])

    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-stopped", shutdown_after="4h", region=None, profile=None)
    assert "stopped" in result.output.lower()
    assert "Starting" in result.output
    mock_tab_up.assert_called_once()


@patch("desk_cli.commands.up.get_default_private_key_path", return_value="/some/key")
@patch("desk_cli.commands.up.tab.tab_up")
@patch("desk_cli.commands.up.start_workstation")
@patch("desk_cli.commands.up.get_instance_state")
@patch("desk_cli.commands.up.list_workstations")
@patch("desk_cli.commands.up.resolve_workstation")
def test_desk_up_waits_for_stopping_instance(
    mock_resolve: object,
    mock_list: object,
    mock_get_state: object,
    mock_start: object,
    mock_tab_up: object,
    _mock_key: object,
) -> None:
    """desk up waits for stopping instance to stop, then starts it."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopping", name="main", state="stopping"),
    ]
    # Simulate instance transitioning: stopping -> stopping -> stopped
    mock_get_state.side_effect = ["stopping", "stopped"]
    mock_start.return_value = ("i-stopping", "2026-02-07T20:00:00Z")

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "main"])

    assert result.exit_code == 0
    assert mock_get_state.call_count >= 1
    mock_start.assert_called_once_with("i-stopping", shutdown_after="4h", region=None, profile=None)
    mock_tab_up.assert_called_once()


@patch("desk_cli.commands.up.get_default_private_key_path", return_value=None)
@patch("desk_cli.commands.up.start_workstation")
@patch("desk_cli.commands.up.list_workstations")
@patch("desk_cli.commands.up.resolve_workstation")
def test_desk_up_skips_connect_when_no_ssh_key(
    mock_resolve: object,
    mock_list: object,
    mock_start: object,
    mock_get_key: object,
) -> None:
    """desk up skips connect and prints instructions when no default SSH key."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="main", state="stopped"),
    ]
    mock_start.return_value = ("i-stopped", None)

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "main"])

    assert result.exit_code == 0
    assert "No SSH key found" in result.output
    assert "desk tab up main" in result.output


def test_desk_create_help() -> None:
    """desk create --help succeeds and shows usage."""
    result = _run_desk("create", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a new workstation instance" in output
    assert "WORKSTATION" in output


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_rejects_duplicate_name_running(mock_create: object) -> None:
    """desk create fails when workstation with same name is running."""
    mock_create.side_effect = ValueError(
        "Workstation named 'main' already exists: i-existing (running). "
        "Use a different name or terminate the existing workstation first."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "main"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "i-existing" in result.output
    assert "running" in result.output


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_rejects_duplicate_name_stopped(mock_create: object) -> None:
    """desk create fails when workstation with same name is stopped."""
    mock_create.side_effect = ValueError(
        "Workstation named 'myws' already exists: i-stopped (stopped). "
        "Use a different name or terminate the existing workstation first."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "myws"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "i-stopped" in result.output
    assert "stopped" in result.output


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_rejects_duplicate_name_stopping(mock_create: object) -> None:
    """desk create fails when workstation with same name is stopping."""
    mock_create.side_effect = ValueError(
        "Workstation named 'myws' already exists: i-stopping (stopping). "
        "Use a different name or terminate the existing workstation first."
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "myws"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "stopping" in result.output


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_allows_duplicate_name_when_terminated(mock_create: object) -> None:
    """desk create succeeds when only terminated workstations have same name."""
    mock_create.return_value = ("i-new123", "2026-02-07T20:00:00Z")

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "main"])

    assert result.exit_code == 0
    mock_create.assert_called_once()
    assert "created successfully" in result.output


def test_desk_help() -> None:
    """desk --help succeeds."""
    result = _run_desk("--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Manage EC2 instances" in output
    assert "up" in output
    assert "create" in output
    assert "list" in output
    assert "stop" in output
    assert "connect" in output


def test_desk_ami_help() -> None:
    """desk ami --help succeeds."""
    result = _run_desk("ami", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Manage AMIs" in output
    assert "create" in output
    assert "list" in output


def test_desk_ami_list_help() -> None:
    """desk ami list --help succeeds."""
    result = _run_desk("ami", "list", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "List AMIs" in output
    assert "table" in output
    assert "plain" in output
    assert "--all" in output


@patch("desk_cli.commands.ami.list_amis")
def test_desk_ami_list_empty(mock_list_amis: object) -> None:
    """desk ami list shows message when no AMIs found."""
    mock_list_amis.return_value = []

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "list"])

    assert result.exit_code == 0
    assert "No AMIs found" in result.output
    mock_list_amis.assert_called_once_with(region=None, profile=None, managed_only=True)


@patch("desk_cli.commands.ami.list_amis")
def test_desk_ami_list_success(mock_list_amis: object) -> None:
    """desk ami list shows table of AMIs."""
    from desk.aws import AmiInfo

    mock_list_amis.return_value = [
        AmiInfo(
            image_id="ami-123",
            name="main-20250201-120000",
            state="available",
            creation_date="2025-02-01T12:00:00.000Z",
            source_instance="i-abc",
        ),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "list"])

    assert result.exit_code == 0
    assert "IMAGE ID" in result.output
    assert "ami-123" in result.output
    assert "main-20250201-120000" in result.output
    assert "available" in result.output
    assert "i-abc" in result.output
    mock_list_amis.assert_called_once_with(region=None, profile=None, managed_only=True)


@patch("desk_cli.commands.ami.list_amis")
def test_desk_ami_list_plain(mock_list_amis: object) -> None:
    """desk ami list --output plain prints tab-separated lines."""
    from desk.aws import AmiInfo

    mock_list_amis.return_value = [
        AmiInfo(
            image_id="ami-456",
            name="custom",
            state="pending",
            creation_date="2025-02-02T00:00:00.000Z",
            source_instance=None,
        ),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "list", "--output", "plain"])

    assert result.exit_code == 0
    assert "ami-456\tcustom\tpending\t2025-02-02T00:00:00.000Z\t-" in result.output
    mock_list_amis.assert_called_once()


@patch("desk_cli.commands.ami.list_amis")
def test_desk_ami_list_all_flag(mock_list_amis: object) -> None:
    """desk ami list --all passes managed_only=False."""
    mock_list_amis.return_value = []

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "list", "--all"])

    assert result.exit_code == 0
    mock_list_amis.assert_called_once_with(region=None, profile=None, managed_only=False)


def test_desk_ami_build_help() -> None:
    """desk ami build --help succeeds."""
    result = _run_desk("ami", "build", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "run" in output
    assert "create" in output
    assert "status" in output
    assert "step" in output


def test_desk_ami_build_run_help() -> None:
    """desk ami build run --help succeeds."""
    result = _run_desk("ami", "build", "run", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "CONFIG_FILE" in output
    assert "copy" in output
    assert "desk create" in output or "create" in output


def test_desk_ami_build_missing_config() -> None:
    """desk ami build run fails when config file does not exist."""
    result = _run_desk("ami", "build", "run", "/nonexistent/config.json")
    assert result.returncode != 0
    assert "No such file" in result.stderr or "nonexistent" in result.stderr.lower()


@patch("desk_cli.commands.ami.terminate_instance")
@patch("desk_cli.commands.ami.wait_for_ami_available", return_value=True)
@patch("desk_cli.commands.ami.create_ami", return_value="ami-12345")
@patch("desk_cli.commands.ami.wait_for_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.resolve_workstation", return_value="i-builder")
@patch("desk_cli.commands.ami.create_workstation")
@patch("desk_cli.commands.ami.get_latest_ubuntu_ami", return_value="ami-ubuntu")
def test_ami_build_uses_sdk_instead_of_nested_desk_process(
    _mock_latest: object,
    mock_create_ws: object,
    _mock_resolve: object,
    _mock_wait_ssm: object,
    _mock_create_ami: object,
    _mock_wait_ami: object,
    _mock_terminate: object,
    tmp_path,
) -> None:
    """AMI build should call SDK helpers directly."""
    import json

    config_path = tmp_path / "ami-config.json"
    config_path.write_text(json.dumps({"ami_name": "my-ami", "steps": []}))

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "run", str(config_path)])

    assert result.exit_code == 0
    mock_create_ws.assert_called_once()


def test_desk_ami_build_invalid_config(tmp_path: object) -> None:
    """desk ami build run fails when config is invalid JSON or schema."""
    from pathlib import Path

    path = Path(tmp_path) / "config.json"
    path.write_text("not json")
    result = _run_desk("ami", "build", "run", str(path))
    assert result.returncode != 0

    path.write_text('{"copy": "not a list"}')
    result = _run_desk("ami", "build", "run", str(path))
    assert result.returncode != 0
    out = _output(result)
    assert "config" in out.lower() or "copy" in out.lower() or "run" in out.lower() or "error" in out.lower()

    path.write_text('{"copy": [], "run": []}')
    result = _run_desk("ami", "build", "run", str(path))
    assert result.returncode != 0
    out = _output(result)
    assert "ami_name" in out.lower()

    path.write_text('{"copy": [], "run": [], "ami_name": "x", "workstation_name": "builder"}')
    result = _run_desk("ami", "build", "run", str(path))
    assert result.returncode != 0
    out = _output(result)
    assert "workstation_name" in out.lower()


def test_desk_ami_build_list_help() -> None:
    """desk ami build list --help mentions archived flag."""
    result = _run_desk("ami", "build", "list", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "--archived" in output


@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
@patch("desk_cli.commands.ami._new_build_id", return_value="20260101-010101-abcdef01")
def test_ami_build_create_uploads(
    _mock_new_id: object,
    _mock_bucket: object,
    mock_session: object,
    tmp_path: object,
) -> None:
    """ami build create uploads artifacts and writes config/manifest to S3."""
    from pathlib import Path

    p = Path(tmp_path)
    (p / "hello.txt").write_text("hi")
    cfg = p / "recipe.json"
    cfg.write_text(
        json.dumps(
            {
                "ami_name": "my-ami",
                "steps": [{"copy": {"source": "hello.txt", "dest": "/tmp/"}}],
            }
        )
    )

    s3 = MagicMock()
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "create", str(cfg)])

    assert result.exit_code == 0
    s3.upload_file.assert_called()
    assert s3.put_object.call_count == 2


@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_list_active(_mock_bucket: object, mock_session: object) -> None:
    """ami build list reads manifests under ami-builds/."""
    s3 = MagicMock()
    mock_session.return_value.client.return_value = s3
    s3.list_objects_v2.return_value = {"CommonPrefixes": [{"Prefix": "ami-builds/b1/"}]}
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b'{"ami_name":"n","created_at":"t"}'),
    }

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "list"])

    assert result.exit_code == 0
    assert "b1" in result.output
    assert "n" in result.output


def _s3_get_for_async_build(
    *,
    has_builder_record: bool,
    instance_id: str | None = None,
    config: dict | None = None,
) -> MagicMock:
    """Return a mock S3 client get_object that serves config.json and optional builder-instance.json."""

    cfg = {"ami_name": "my-ami", "instance_type": "t3.medium"}
    if config is not None:
        cfg = {**cfg, **config}

    def get_object(Bucket: object, Key: str) -> dict:
        if Key.endswith("config.json"):
            return {
                "Body": io.BytesIO(
                    json.dumps(cfg).encode()
                )
            }
        if Key.endswith("builder-instance.json"):
            if not has_builder_record:
                raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "n"}}, "GetObject")
            assert instance_id is not None
            return {
                "Body": io.BytesIO(json.dumps({"instance_id": instance_id}).encode()),
            }
        raise AssertionError(Key)

    s3 = MagicMock()
    s3.get_object.side_effect = get_object
    return s3


@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_status_no_builder_record(_mock_bucket: object, mock_session: object) -> None:
    """ami build status shows next step when builder-instance.json is absent."""
    s3 = _s3_get_for_async_build(has_builder_record=False)
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "status", "b1"])

    assert result.exit_code == 0
    assert "not recorded" in result.output.lower() or "not recorded yet" in result.output.lower()
    assert "builder-instance.json" in result.output


@patch("desk_cli.commands.ami.is_ssm_ready", return_value=False)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_status_running_not_ssm(
    _mock_bucket: object, mock_session: object, _mock_state: object, _mock_ssm: object
) -> None:
    """ami build status reports SSM not ready when EC2 is running."""
    s3 = _s3_get_for_async_build(has_builder_record=True, instance_id="i-abc")
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "status", "b1"])

    assert result.exit_code == 0
    assert "SSM" in result.output
    assert "no" in result.output.lower() or "not ready" in result.output.lower()


@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_status_recipe_ssm_ready(
    _mock_bucket: object, mock_session: object, _mock_state: object, _mock_ssm: object, _mock_list: object
) -> None:
    """ami build status shows recipe section when SSM is ready."""
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={
            "steps": [
                {"run": "echo hi"},
                {"copy": {"source": "s3:/ami-builds/b1/files/copy/0/x", "dest": "/tmp/x"}},
            ]
        },
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "status", "b1"])

    assert result.exit_code == 0
    assert "Recipe:" in result.output
    assert "Steps in config: 2" in result.output
    assert "Next: step 0" in result.output
    assert "echo hi" in result.output


@patch("desk_cli.commands.ami._evaluate_async_recipe")
@patch("desk_cli.commands.ami.send_ssm_command", return_value="cmd-ssm-1")
@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_evaluates_recipe_once(
    _mock_bucket: object,
    mock_session: object,
    _mock_state: object,
    _mock_ssm: object,
    _mock_list: object,
    mock_send: object,
    mock_eval: object,
) -> None:
    """ami build step calls _evaluate_async_recipe only once (shared with status print)."""
    from desk_cli.commands.ami import AsyncRecipeEval

    mock_eval.return_value = AsyncRecipeEval(
        total_steps=1,
        steps=({"run": "echo hello"},),
        blocked=False,
        blocked_step_index=None,
        last_error=None,
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=0,
        recipe_complete=False,
    )
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={"steps": [{"run": "echo hello"}]},
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1"])

    assert result.exit_code == 0
    mock_eval.assert_called_once()
    mock_send.assert_called_once()


@patch("desk_cli.commands.ami.send_ssm_command", return_value="cmd-ssm-1")
@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_starts_recipe_run(
    _mock_bucket: object,
    mock_session: object,
    _mock_state: object,
    _mock_ssm: object,
    _mock_list: object,
    mock_send: object,
) -> None:
    """ami build step sends async SSM for first recipe step when idle."""
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={"steps": [{"run": "echo hello"}]},
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    _args, kwargs = mock_send.call_args
    assert _args[0] == "i-abc"
    assert "echo hello" in _args[1]
    assert kwargs.get("comment") is not None
    assert "cmd-ssm-1" in result.output


@patch("desk_cli.commands.ami.generate_presigned_get_object_url", return_value="https://example.com/presigned")
@patch("desk_cli.commands.ami.send_ssm_command", return_value="cmd-ssm-copy")
@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_copy_uses_curl_presigned_url(
    _mock_bucket: object,
    mock_session: object,
    _mock_state: object,
    _mock_ssm: object,
    _mock_list: object,
    mock_send: object,
    _mock_presign: object,
) -> None:
    """Copy steps download via curl and a presigned URL (no AWS CLI on the builder)."""
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={
            "steps": [
                {
                    "copy": {
                        "source": "s3:/ami-builds/b1/files/copy/0/file.sh",
                        "dest": "/tmp/file.sh",
                    }
                },
            ]
        },
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    _args, _kwargs = mock_send.call_args
    script = _args[1]
    assert "curl -fsSL" in script
    assert "https://example.com/presigned" in script
    assert "chmod +x" in script
    assert "aws s3" not in script


@patch("desk_cli.commands.ami.send_ssm_command", return_value="cmd-retry")
@patch("desk_cli.commands.ami._evaluate_async_recipe")
@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_retry_after_failure(
    _mock_bucket: object,
    mock_session: object,
    _mock_state: object,
    _mock_ssm: object,
    _mock_list: object,
    mock_eval: object,
    mock_send: object,
) -> None:
    """--retry re-sends SSM for the failed step index."""
    from desk_cli.commands.ami import AsyncRecipeEval

    mock_eval.return_value = AsyncRecipeEval(
        total_steps=1,
        steps=({"run": "echo hi"},),
        blocked=True,
        blocked_step_index=0,
        last_error="status='Failed'",
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=None,
        recipe_complete=False,
    )
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={"steps": [{"run": "echo hi"}]},
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1", "--retry"])

    assert result.exit_code == 0
    assert "Retrying recipe step 0" in result.output
    mock_send.assert_called_once()


@patch("desk_cli.commands.ami._evaluate_async_recipe")
@patch("desk_cli.commands.ami.list_command_invocations_for_instance", return_value=[])
@patch("desk_cli.commands.ami.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.ami.get_instance_state", return_value="running")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_retry_requires_failed_step(
    _mock_bucket: object,
    mock_session: object,
    _mock_state: object,
    _mock_ssm: object,
    _mock_list: object,
    mock_eval: object,
) -> None:
    """--retry errors when there is no failed step."""
    from desk_cli.commands.ami import AsyncRecipeEval

    mock_eval.return_value = AsyncRecipeEval(
        total_steps=1,
        steps=({"run": "echo hi"},),
        blocked=False,
        blocked_step_index=None,
        last_error=None,
        in_progress_step_index=None,
        in_progress_command_id=None,
        next_step_index=0,
        recipe_complete=False,
    )
    s3 = _s3_get_for_async_build(
        has_builder_record=True,
        instance_id="i-abc",
        config={"steps": [{"run": "echo hi"}]},
    )
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1", "--retry"])

    assert result.exit_code != 0
    assert "Nothing to retry" in result.output


@patch("desk_cli.commands.ami._put_s3_object_json")
@patch("desk_cli.commands.ami.create_workstation", return_value=("i-new", None))
@patch("desk_cli.commands.ami.get_latest_ubuntu_ami", return_value="ami-ubuntu")
@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_step_creates_and_records_instance(
    _mock_bucket: object,
    mock_session: object,
    _mock_ubuntu: object,
    mock_create: object,
    mock_put: object,
) -> None:
    """ami build step creates workstation and writes builder-instance.json to S3."""
    s3 = _s3_get_for_async_build(has_builder_record=False)
    mock_session.return_value.client.return_value = s3

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "step", "b1"])

    assert result.exit_code == 0
    mock_create.assert_called_once()
    _args, kwargs = mock_create.call_args
    assert kwargs.get("shutdown_after") == "4h"
    mock_put.assert_called_once()
    args, kwargs = mock_put.call_args
    assert "builder-instance.json" in args[2]


@patch("desk_cli.commands.ami.boto3.Session")
@patch("desk_cli.commands.ami.get_desk_copy_bucket", return_value="test-bucket")
def test_ami_build_cancel_archives(_mock_bucket: object, mock_session: object) -> None:
    """ami build cancel copies keys to ami-build-archive/ then deletes."""
    s3 = MagicMock()
    mock_session.return_value.client.return_value = s3
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "ami-builds/bid/manifest.json"}]},
    ]
    s3.get_paginator.return_value = paginator

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "build", "cancel", "bid"])

    assert result.exit_code == 0
    s3.copy_object.assert_called_once()
    s3.delete_object.assert_called_once()


def test_desk_ami_create_help() -> None:
    """desk ami create --help succeeds."""
    result = _run_desk("ami", "create", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create an AMI from a workstation" in output
    assert "WORKSTATION" in output
    assert "--name" in output
    assert "--no-reboot" in output
    assert "--wait" in output


@patch("desk_cli.commands.ami.get_ami_state")
@patch("desk_cli.commands.ami.create_ami")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_success(
    mock_resolve: object,
    mock_get_state: object,
    mock_create_ami: object,
    mock_ami_state: object,
) -> None:
    """desk ami create creates AMI and waits for it."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "running"
    mock_create_ami.return_value = "ami-12345"
    mock_ami_state.side_effect = ["pending", "available"]

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main", "--name", "my-ami"])

    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "main", region=None, profile=None,
        states=["pending", "running", "stopping", "stopped"]
    )
    mock_create_ami.assert_called_once_with(
        instance_id="i-abc123",
        name="my-ami",
        description=None,
        no_reboot=False,
        region=None,
        profile=None,
    )
    assert "ami-12345" in result.output
    assert "created successfully" in result.output


@patch("desk_cli.commands.ami.get_ami_state")
@patch("desk_cli.commands.ami.create_ami")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_no_wait(
    mock_resolve: object,
    mock_get_state: object,
    mock_create_ami: object,
    mock_ami_state: object,
) -> None:
    """desk ami create --no-wait returns immediately."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "stopped"
    mock_create_ami.return_value = "ami-12345"

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main", "--no-wait"])

    assert result.exit_code == 0
    mock_create_ami.assert_called_once()
    mock_ami_state.assert_not_called()
    assert "being created in the background" in result.output


@patch("desk_cli.commands.ami.create_ami")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_with_no_reboot(
    mock_resolve: object,
    mock_get_state: object,
    mock_create_ami: object,
) -> None:
    """desk ami create --no-reboot passes no_reboot flag."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "running"
    mock_create_ami.return_value = "ami-12345"

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main", "--no-reboot", "--no-wait"])

    assert result.exit_code == 0
    mock_create_ami.assert_called_once()
    call_kwargs = mock_create_ami.call_args[1]
    assert call_kwargs["no_reboot"] is True
    assert "inconsistent state" in result.output


@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_workstation_not_found(mock_resolve: object) -> None:
    """desk ami create fails when workstation not found."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "unknown"])

    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.ami.wait_for_instance_state")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_waits_for_stopping_instance(
    mock_resolve: object,
    mock_get_state: object,
    mock_wait_state: object,
) -> None:
    """desk ami create waits for stopping instance to stop."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "stopping"
    mock_wait_state.return_value = False  # Timeout

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main"])

    assert result.exit_code != 0
    assert "Timed out" in result.output
    mock_wait_state.assert_called_once()


@patch("desk_cli.commands.ami.get_ami_state")
@patch("desk_cli.commands.ami.create_ami")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_generates_default_name(
    mock_resolve: object,
    mock_get_state: object,
    mock_create_ami: object,
    mock_ami_state: object,
) -> None:
    """desk ami create generates timestamp-based name when not provided."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "stopped"
    mock_create_ami.return_value = "ami-12345"
    mock_ami_state.return_value = "available"

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main"])

    assert result.exit_code == 0
    mock_create_ami.assert_called_once()
    # Check that name was generated with workstation name and timestamp
    call_kwargs = mock_create_ami.call_args[1]
    assert call_kwargs["name"].startswith("main-")
    # Name should have format main-YYYYMMDD-HHMMSS
    assert len(call_kwargs["name"]) > len("main-")


@patch("desk_cli.commands.ami.get_ami_state")
@patch("desk_cli.commands.ami.create_ami")
@patch("desk_cli.commands.ami.get_instance_state")
@patch("desk_cli.commands.ami.resolve_workstation")
def test_desk_ami_create_fails_on_ami_failure(
    mock_resolve: object,
    mock_get_state: object,
    mock_create_ami: object,
    mock_ami_state: object,
) -> None:
    """desk ami create fails when AMI creation fails."""
    mock_resolve.return_value = "i-abc123"
    mock_get_state.return_value = "stopped"
    mock_create_ami.return_value = "ami-12345"
    mock_ami_state.return_value = "failed"

    runner = CliRunner()
    result = runner.invoke(cli, ["ami", "create", "main"])

    assert result.exit_code != 0
    assert "failed" in result.output


def test_desk_shows_friendly_error_without_traceback() -> None:
    """Unexpected exceptions show Error: message, not full traceback via main()."""
    from io import StringIO
    from unittest.mock import patch

    # Test the main() wrapper function's exception handling
    with patch("desk_cli.cli.cli") as mock_cli:
        mock_cli.side_effect = RuntimeError("Something went wrong")

        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            with patch("sys.exit") as mock_exit:
                from desk_cli.cli import main
                main()

                mock_exit.assert_called_once_with(1)

        output = mock_stderr.getvalue()
        assert "Error: Something went wrong" in output
        assert "Traceback" not in output


def test_desk_version() -> None:
    """desk --version prints version."""
    result = _run_desk("--version")
    assert result.returncode == 0
    output = _output(result)
    assert "desk, version 0.1.0" in output


def test_desk_list_help() -> None:
    """desk list --help succeeds."""
    result = _run_desk("list", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "List workstation instances" in output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_empty(mock_list: object) -> None:
    """desk list shows message when no workstations."""
    mock_list.return_value = []
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "No workstations found" in result.output


def test_desk_connect_help() -> None:
    """desk connect --help succeeds."""
    result = _run_desk("connect", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Connect to a workstation via SSH" in output
    assert "WORKSTATION" in output
    assert "--forward" in output or "-L" in output
    assert "--forward-agent" in output or "-A" in output


def test_desk_keygen_help() -> None:
    """desk keygen --help succeeds."""
    result = _run_desk("keygen", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Generate an SSH key" in output
    assert "ed25519" in output
    assert "--force" in output


@patch("desk_cli.commands.keygen.subprocess.run")
def test_desk_keygen_creates_key(mock_run: object, tmp_path) -> None:
    """desk keygen -f PATH creates key and prints public key."""
    key_path = tmp_path / "id_ed25519"
    pub_path = tmp_path / "id_ed25519.pub"
    pub_content = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample user@host"

    def run_side_effect(*args: object, **kwargs: object) -> object:
        # Simulate ssh-keygen creating the files
        key_path.write_text("private")
        pub_path.write_text(pub_content + "\n")
        from subprocess import CompletedProcess
        return CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

    mock_run.side_effect = run_side_effect

    runner = CliRunner()
    result = runner.invoke(cli, ["keygen", "-f", str(key_path)])

    assert result.exit_code == 0
    assert f"Created {key_path}" in result.output
    assert "Public key:" in result.output
    assert pub_content in result.output
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "ssh-keygen" in call_args
    assert "-t" in call_args and "ed25519" in call_args
    assert "-f" in call_args and str(key_path) in call_args
    assert "-N" in call_args and "" in call_args


def test_desk_keygen_refuses_overwrite(tmp_path) -> None:
    """desk keygen fails when key exists and --force not given."""
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("existing")

    runner = CliRunner()
    result = runner.invoke(cli, ["keygen", "-f", str(key_path)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "force" in result.output.lower()


@patch("desk_cli.commands.keygen.subprocess.run")
def test_desk_keygen_force_overwrite(mock_run: object, tmp_path) -> None:
    """desk keygen --force overwrites existing key."""
    key_path = tmp_path / "id_ed25519"
    pub_path = tmp_path / "id_ed25519.pub"
    key_path.write_text("old")
    pub_content = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInew user@host"

    def run_side_effect(*args: object, **kwargs: object) -> object:
        key_path.write_text("new")
        pub_path.write_text(pub_content + "\n")
        from subprocess import CompletedProcess
        return CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

    mock_run.side_effect = run_side_effect

    runner = CliRunner()
    result = runner.invoke(cli, ["keygen", "-f", str(key_path), "--force"])

    assert result.exit_code == 0
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "-t" in call_args and "ed25519" in call_args


@patch("desk_cli.commands.keygen.subprocess.run")
def test_desk_keygen_type_rsa(mock_run: object, tmp_path) -> None:
    """desk keygen --type rsa passes -b 4096 to ssh-keygen."""
    key_path = tmp_path / "id_rsa"
    pub_path = tmp_path / "id_rsa.pub"
    pub_path.write_text("ssh-rsa AAAAB3... user@host\n")

    mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    runner = CliRunner()
    result = runner.invoke(cli, ["keygen", "-f", str(key_path), "--type", "rsa"])

    assert result.exit_code == 0
    call_args = mock_run.call_args[0][0]
    assert "ed25519" not in call_args
    assert "-t" in call_args and "rsa" in call_args
    assert "-b" in call_args and "4096" in call_args


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_resolves_and_execs_ssh(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    mock_get_public_key: object,
    mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect injects key via SSM, then execs ssh with ProxyCommand."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True  # SSM ready, skip wait
    mock_get_public_key.return_value = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 test"
    mock_add_key.return_value = "cmd-123"
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max"])

    mock_resolve.assert_called_once_with("max", region=None, profile=None)
    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert args[0] == "ssh"
    assert "ProxyCommand=" in " ".join(args)
    assert "AWS-StartSSHSession" in " ".join(args)
    assert "ubuntu@i-abc123" in args


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_with_key(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    mock_get_public_key: object,
    mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect --key uses desk-managed key path."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_get_public_key.return_value = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 test"
    mock_add_key.return_value = "cmd-123"
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "my-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "-i", str(key_file)])

    args = mock_execvp.call_args[0][1]
    assert "-i" in args
    idx = args.index("-i")
    assert args[idx + 1] == str(key_file)


@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_key_not_found(mock_get_default_key: object) -> None:
    """desk connect fails when default key file does not exist."""
    mock_get_default_key.return_value = "/nonexistent/my-key.pem"
    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... ssm")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_waits_for_ssm_then_connects(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_execvp: object,
    mock_is_ssm_ready: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect waits when SSM not ready, then proceeds."""
    mock_resolve.return_value = "i-abc123"
    mock_is_ssm_ready.side_effect = [False, False, True]  # Ready on 3rd check
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "--wait-timeout", "10"])
    assert mock_is_ssm_ready.call_count == 3
    mock_execvp.assert_called_once()


@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_not_found(
    mock_get_default_key: object, mock_resolve: object, tmp_path
) -> None:
    """desk connect with unknown workstation shows error."""
    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    mock_resolve.side_effect = ValueError("Workstation 'x' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "x"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... ssm")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_with_port_forward(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect --forward adds -L flag to ssh."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "-L", "8080:localhost:80"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-L" in args
    idx = args.index("-L")
    assert args[idx + 1] == "8080:localhost:80"


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... ssm")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_with_multiple_port_forwards(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect with multiple --forward options adds all -L flags."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["connect", "max", "-L", "8080:localhost:80", "--forward", "3000:localhost:3000"]
    )

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    # Both port forwards should be in the args
    assert args.count("-L") == 2
    assert "8080:localhost:80" in args
    assert "3000:localhost:3000" in args


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... ssm")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_with_forward_agent(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect -A passes -A to ssh."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "-A"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-A" in args


@patch("desk_cli.commands.connect.add_temporary_ssh_key")
@patch("desk_cli.commands.connect.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... ssm")
@patch("desk_cli.commands.connect.os.execvp")
@patch("desk_cli.commands.connect.is_ssm_ready")
@patch("desk_cli.commands.connect.resolve_workstation")
@patch("desk_cli.commands.connect.get_default_private_key_path")
def test_desk_connect_with_forward_agent_long_option(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk connect --forward-agent passes -A to ssh."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "--forward-agent"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-A" in args


def test_desk_stop_help() -> None:
    """desk stop --help succeeds."""
    result = _run_desk("stop", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Stop a workstation instance" in output
    assert "WORKSTATION" in output


@patch("desk_cli.commands.stop.stop_instance")
@patch("desk_cli.commands.stop.resolve_workstation")
def test_desk_stop_by_name(mock_resolve: object, mock_stop: object) -> None:
    """desk stop resolves name and stops instance."""
    mock_resolve.return_value = "i-abc123"
    mock_stop.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "max"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("max", region=None, profile=None)
    mock_stop.assert_called_once_with("i-abc123", region=None, profile=None)
    assert "Stopped" in result.output


@patch("desk_cli.commands.stop.stop_instance")
@patch("desk_cli.commands.stop.resolve_workstation")
def test_desk_stop_by_instance_id(mock_resolve: object, mock_stop: object) -> None:
    """desk stop with instance ID stops the instance."""
    mock_resolve.return_value = "i-abc123"
    mock_stop.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "i-abc123"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("i-abc123", region=None, profile=None)
    mock_stop.assert_called_once_with("i-abc123", region=None, profile=None)


@patch("desk_cli.commands.stop.resolve_workstation")
def test_desk_stop_not_found(mock_resolve: object) -> None:
    """desk stop with unknown name shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_desk_kill_help() -> None:
    """desk kill --help succeeds."""
    result = _run_desk("kill", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Terminate a workstation instance" in output
    assert "WORKSTATION" in output
    assert "--yes" in output


@patch("desk_cli.commands.kill.terminate_instance")
@patch("desk_cli.commands.kill.resolve_workstation")
def test_desk_kill_with_yes_flag(mock_resolve: object, mock_terminate: object) -> None:
    """desk kill --yes terminates without prompting."""
    mock_resolve.return_value = "i-abc123"
    mock_terminate.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "max", "--yes"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "max", region=None, profile=None, states=["pending", "running", "stopping", "stopped"]
    )
    mock_terminate.assert_called_once_with("i-abc123", region=None, profile=None)
    assert "Terminated" in result.output


@patch("desk_cli.commands.kill.terminate_instance")
@patch("desk_cli.commands.kill.resolve_workstation")
def test_desk_kill_confirms_before_terminate(mock_resolve: object, mock_terminate: object) -> None:
    """desk kill prompts for confirmation."""
    mock_resolve.return_value = "i-abc123"
    mock_terminate.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "max"], input="y\n")
    assert result.exit_code == 0
    mock_terminate.assert_called_once()
    assert "Terminate" in result.output


@patch("desk_cli.commands.kill.terminate_instance")
@patch("desk_cli.commands.kill.resolve_workstation")
def test_desk_kill_aborts_on_no(mock_resolve: object, mock_terminate: object) -> None:
    """desk kill aborts when user declines confirmation."""
    mock_resolve.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "max"], input="n\n")
    assert result.exit_code != 0
    mock_terminate.assert_not_called()


@patch("desk_cli.commands.kill.resolve_workstation")
def test_desk_kill_not_found(mock_resolve: object) -> None:
    """desk kill with unknown name shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "unknown", "--yes"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_desk_start_help() -> None:
    """desk start --help succeeds."""
    result = _run_desk("start", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Start a stopped workstation instance" in output
    assert "WORKSTATION" in output


@patch("desk_cli.commands.start.start_workstation")
@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_by_name(
    mock_resolve: object, mock_start: object
) -> None:
    """desk start resolves name and starts instance."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = ("i-abc123", "2026-02-07T20:00:00Z")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "max"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "max", region=None, profile=None, states=["stopped"]
    )
    mock_start.assert_called_once_with("i-abc123", shutdown_after="4h", region=None, profile=None)
    assert "Started" in result.output


@patch("desk_cli.commands.start.start_workstation")
@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_by_instance_id(
    mock_resolve: object, mock_start: object
) -> None:
    """desk start with instance ID starts the instance."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = ("i-abc123", "2026-02-07T20:00:00Z")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "i-abc123"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "i-abc123", region=None, profile=None, states=["stopped"]
    )
    mock_start.assert_called_once_with("i-abc123", shutdown_after="4h", region=None, profile=None)


@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_not_found(mock_resolve: object) -> None:
    """desk start with unknown name shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_table_output(mock_list: object) -> None:
    """desk list shows table of workstations."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-abc123", name="max", state="running"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "i-abc123" in result.output
    assert "max" in result.output
    assert "running" in result.output


def test_desk_run_help() -> None:
    """desk run --help succeeds."""
    result = _run_desk("run", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Run a script on a workstation via SSM" in output
    assert "WORKSTATION" in output
    assert "SCRIPT" in output
    assert "--follow" in output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_sends_command(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run sends command via SSM and returns when started."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo hello"])

    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_send.assert_called_once()
    assert "echo hello" in mock_send.call_args[0]
    assert "Command is running" in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_follow_tails_output(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run --follow tails output until completion."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"

    # Simulate output appearing over time
    mock_get_invocation.side_effect = [
        CommandResult(
            command_id="cmd-12345",
            status="InProgress",
            stdout="line1\n",
            stderr="",
            exit_code=None,
        ),
        CommandResult(
            command_id="cmd-12345",
            status="InProgress",
            stdout="line1\nline2\n",
            stderr="",
            exit_code=None,
        ),
        CommandResult(
            command_id="cmd-12345",
            status="Success",
            stdout="line1\nline2\nline3\n",
            stderr="",
            exit_code=0,
        ),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo hello", "--follow"])

    assert result.exit_code == 0
    # Output should contain the streamed lines
    assert "line1" in result.output
    assert "line2" in result.output
    assert "line3" in result.output
    assert "completed successfully" in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_follow_shows_stderr(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run --follow shows stderr output."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"

    mock_get_invocation.side_effect = [
        CommandResult(
            command_id="cmd-12345",
            status="InProgress",
            stdout="",
            stderr="error output\n",
            exit_code=None,
        ),
        CommandResult(
            command_id="cmd-12345",
            status="Failed",
            stdout="",
            stderr="error output\n",
            exit_code=1,
        ),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "bad-command", "--follow"])

    assert result.exit_code == 1
    # stderr output should be in the combined output
    assert "error output" in result.output


@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_not_ssm_ready_no_wait(
    mock_resolve: object,
    mock_ssm_ready: object,
) -> None:
    """desk run fails when SSM not ready and --no-wait specified."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = False

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo hello", "--no-wait"])

    assert result.exit_code != 0
    assert "not SSM-ready" in result.output


@patch("desk_cli.commands.run.wait_for_ssm_ready")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_waits_for_ssm(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_wait: object,
) -> None:
    """desk run waits for SSM when not ready and wait=True."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = False
    mock_wait.return_value = False  # Timeout

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo hello", "--wait-timeout", "5"])

    assert result.exit_code != 0
    mock_wait.assert_called_once()
    assert "did not become SSM-ready" in result.output


@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_workstation_not_found(mock_resolve: object) -> None:
    """desk run fails when workstation not found."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "unknown", "echo hello"])

    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_immediate_completion(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run handles immediate command completion."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="Success",
        stdout="done\n",
        stderr="",
        exit_code=0,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo done"])

    assert result.exit_code == 0
    assert "done" in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_command_failure_exits_nonzero(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run exits with nonzero code on command failure."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="Failed",
        stdout="",
        stderr="error\n",
        exit_code=1,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "exit 1"])

    assert result.exit_code != 0
    assert "failed" in result.output.lower()


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_reads_local_script_file(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
    tmp_path,
) -> None:
    """desk run reads script content from local file."""
    from desk.aws import CommandResult

    # Create a local script file
    script_file = tmp_path / "test_script.sh"
    script_file.write_text("#!/bin/bash\necho 'hello from script'\nexit 0\n")

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", str(script_file)])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    # Verify the file contents were sent, not the file path
    sent_script = mock_send.call_args[0][1]
    assert "echo 'hello from script'" in sent_script
    assert "#!/bin/bash" in sent_script
    assert "Reading script from" in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_inline_command_not_treated_as_file(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run treats non-file arguments as inline commands."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo hello && date"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    # Verify the inline command was sent directly
    sent_script = mock_send.call_args[0][1]
    assert sent_script == "echo hello && date"
    assert "Reading script from" not in result.output


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_as_user(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run --user wraps command to run as specified user."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "whoami", "--user", "ubuntu"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    sent_script = mock_send.call_args[0][1]
    # Should be wrapped with sudo -u
    assert "sudo -u ubuntu" in sent_script
    assert "whoami" in sent_script


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_as_user_with_quotes(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
) -> None:
    """desk run --user properly escapes quotes in commands."""
    from desk.aws import CommandResult

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", "echo 'hello world'", "-u", "ubuntu"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    sent_script = mock_send.call_args[0][1]
    assert "sudo -u ubuntu" in sent_script
    # The command should be properly quoted
    assert "hello world" in sent_script


@patch("desk_cli.commands.run.get_command_invocation")
@patch("desk_cli.commands.run.send_ssm_command")
@patch("desk_cli.commands.run.is_ssm_ready")
@patch("desk_cli.commands.run.resolve_workstation")
def test_desk_run_as_user_with_script_file(
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_send: object,
    mock_get_invocation: object,
    tmp_path,
) -> None:
    """desk run --user works with local script files."""
    from desk.aws import CommandResult

    # Create a local script file
    script_file = tmp_path / "test_script.sh"
    script_file.write_text("#!/bin/bash\necho 'running as user'\n")

    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = True
    mock_send.return_value = "cmd-12345"
    mock_get_invocation.return_value = CommandResult(
        command_id="cmd-12345",
        status="InProgress",
        stdout="",
        stderr="",
        exit_code=None,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "main", str(script_file), "--user", "ubuntu"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    sent_script = mock_send.call_args[0][1]
    # Should read file AND wrap with sudo
    assert "sudo -u ubuntu" in sent_script
    assert "running as user" in sent_script


def test_desk_scp_help() -> None:
    """desk scp --help succeeds."""
    result = _run_desk("scp", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Copy files to/from a workstation" in output
    assert "SOURCE" in output
    assert "DESTINATION" in output
    assert "--recursive" in output or "-r" in output


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_upload(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp uploads local file to remote."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "./local.txt", ":~/remote.txt"])

    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert args[0] == "scp"
    assert "ProxyCommand=" in " ".join(args)
    assert "./local.txt" in args
    assert "ubuntu@i-abc123:~/remote.txt" in args


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_download(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp downloads remote file to local."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", ":~/remote.txt", "./local.txt"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "ubuntu@i-abc123:~/remote.txt" in args
    assert "./local.txt" in args


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_with_workstation_prefix(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp with workstation:path format."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "main:/etc/hosts", "./hosts"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "ubuntu@i-abc123:/etc/hosts" in args
    assert "./hosts" in args


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_recursive(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp --recursive adds -r flag."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "-r", "./local-dir", ":~/remote-dir"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    # -r should be in args (but not as part of another option)
    # Find standalone -r
    assert "-r" in args


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_with_custom_key(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp -i uses given identity file."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "my-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "./local.txt", ":~/remote.txt", "-i", str(key_file)])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-i" in args
    idx = args.index("-i")
    assert args[idx + 1] == str(key_file)


@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_key_not_found(mock_get_default_key: object) -> None:
    """desk scp fails when default key file does not exist."""
    mock_get_default_key.return_value = "/nonexistent/my-key.pem"
    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "./local.txt", ":~/remote.txt"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_workstation_not_found(
    mock_get_default_key: object, mock_resolve: object, tmp_path
) -> None:
    """desk scp with unknown workstation shows error."""
    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "unknown", "./local.txt", ":~/remote.txt"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_waits_for_ssm_then_proceeds(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_execvp: object,
    mock_is_ssm_ready: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp waits when SSM not ready, then proceeds."""
    mock_resolve.return_value = "i-abc123"
    mock_is_ssm_ready.side_effect = [False, False, True]
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "./local.txt", ":~/remote.txt", "--wait-timeout", "10"])
    assert mock_is_ssm_ready.call_count == 3
    mock_execvp.assert_called_once()


@patch("desk_cli.commands.scp.add_temporary_ssh_key")
@patch("desk_cli.commands.scp.get_public_key_content", return_value="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...")
@patch("desk_cli.commands.scp.os.execvp")
@patch("desk_cli.commands.scp.is_ssm_ready")
@patch("desk_cli.commands.scp.resolve_workstation")
@patch("desk_cli.commands.scp.get_default_private_key_path")
def test_desk_scp_with_custom_user(
    mock_get_default_key: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    _mock_get_public: object,
    _mock_add_key: object,
    tmp_path,
) -> None:
    """desk scp --user uses custom username."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_default_key.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main", "./local.txt", ":~/remote.txt", "--user", "admin"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "admin@i-abc123:~/remote.txt" in args


# ── shutdown / auto-stop tests ───────────────────────────────────────


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_shows_shutdown_column(mock_list: object) -> None:
    """desk list table includes a SHUTDOWN column."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-abc123", name="main", state="running", shutdown_at="2099-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "SHUTDOWN" in result.output
    # Should show a future relative time
    assert "in " in result.output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_shutdown_overdue_shown(mock_list: object) -> None:
    """desk list shows OVERDUE for past shutdown times."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-abc123", name="main", state="running", shutdown_at="2020-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "OVERDUE" in result.output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_stopped_not_overdue(mock_list: object) -> None:
    """desk list does not show OVERDUE for stopped/stopping instances."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="old", state="stopped", shutdown_at="2020-01-01T00:00:00Z"),
        Workstation(instance_id="i-stopping", name="going", state="stopping", shutdown_at="2020-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "OVERDUE" not in result.output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_no_shutdown_shows_dash(mock_list: object) -> None:
    """desk list shows '-' when no shutdown tag."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-abc123", name="main", state="running", shutdown_at=None),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "SHUTDOWN" in result.output


@patch("desk_cli.commands.list_.list_workstations")
def test_desk_list_plain_includes_shutdown(mock_list: object) -> None:
    """desk list --output plain includes shutdown info."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-abc123", name="main", state="running", shutdown_at="2099-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["list", "--output", "plain"])
    assert result.exit_code == 0
    assert "in " in result.output


@patch("desk_cli.commands.start.start_workstation")
@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_sets_shutdown_tag(
    mock_resolve: object,
    mock_start: object,
) -> None:
    """desk start sets a shutdown tag by default via SDK."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = ("i-abc123", "2026-02-07T20:00:00Z")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main"])
    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-abc123", shutdown_after="4h", region=None, profile=None)


@patch("desk_cli.commands.start.start_workstation")
@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_custom_shutdown_hours(
    mock_resolve: object,
    mock_start: object,
) -> None:
    """desk start --shutdown 8 passes 8h to SDK."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = ("i-abc123", "2026-02-08T00:00:00Z")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main", "--shutdown", "8"])
    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-abc123", shutdown_after="8", region=None, profile=None)


@patch("desk_cli.commands.start.start_workstation")
@patch("desk_cli.commands.start.resolve_workstation")
def test_desk_start_shutdown_zero_skips_tag(
    mock_resolve: object,
    mock_start: object,
) -> None:
    """desk start --shutdown 0 passes 0 to SDK (no tag set)."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = ("i-abc123", None)
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main", "--shutdown", "0"])
    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-abc123", shutdown_after="0", region=None, profile=None)


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_sets_shutdown_tag(mock_create: object) -> None:
    """desk create passes default shutdown_after=4h to SDK."""
    mock_create.return_value = ("i-new123", "2026-02-07T20:00:00Z")

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "main"])
    assert result.exit_code == 0
    mock_create.assert_called_once()
    call_kw = mock_create.call_args[1]
    assert call_kw["shutdown_after"] == "4h"


@patch("desk_cli.commands.create.create_workstation")
def test_desk_create_shutdown_zero_skips_tag(mock_create: object) -> None:
    """desk create --shutdown 0 passes 0 to SDK (no tag set)."""
    mock_create.return_value = ("i-new123", None)

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "main", "--shutdown", "0"])
    assert result.exit_code == 0
    call_kw = mock_create.call_args[1]
    assert call_kw["shutdown_after"] == "0"


def test_desk_auto_stop_help() -> None:
    """desk auto-stop --help succeeds."""
    result = _run_desk("auto-stop", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Set or change the auto-stop time" in output
    assert "WORKSTATION" in output
    assert "DURATION" in output
    assert "--clear" in output


@patch("desk_cli.commands.auto_stop.set_shutdown_tag")
@patch("desk_cli.commands.auto_stop.compute_shutdown_at")
@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_sets_shutdown(
    mock_resolve: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk auto-stop sets shutdown tag on a workstation."""
    mock_resolve.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T20:00:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "main", "6h"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_compute.assert_called_once_with(6.0)
    mock_set_tag.assert_called_once_with(
        "i-abc123", "2026-02-07T20:00:00Z", region=None, profile=None
    )
    assert "Auto-stop set to" in result.output


@patch("desk_cli.commands.auto_stop.set_shutdown_tag")
@patch("desk_cli.commands.auto_stop.compute_shutdown_at")
@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_with_minutes(
    mock_resolve: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk auto-stop main 30m sets shutdown 30 minutes from now."""
    mock_resolve.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T16:30:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "main", "30m"])
    assert result.exit_code == 0
    mock_compute.assert_called_once_with(0.5)
    mock_set_tag.assert_called_once()


@patch("desk_cli.commands.auto_stop.set_shutdown_tag")
@patch("desk_cli.commands.auto_stop.compute_shutdown_at")
@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_with_hours_and_minutes(
    mock_resolve: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk auto-stop main 2h30m sets shutdown 2.5 hours from now."""
    mock_resolve.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T18:30:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "main", "2h30m"])
    assert result.exit_code == 0
    mock_compute.assert_called_once_with(2.5)
    mock_set_tag.assert_called_once()


@patch("desk_cli.commands.auto_stop.set_shutdown_tag")
@patch("desk_cli.commands.auto_stop.compute_shutdown_at")
@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_defaults(
    mock_resolve: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk auto-stop main uses 4h when duration omitted."""
    mock_resolve.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T20:00:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "main"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_compute.assert_called_once_with(4.0)
    mock_set_tag.assert_called_once()


@patch("desk_cli.commands.auto_stop.clear_shutdown_tag")
@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_clear(
    mock_resolve: object,
    mock_clear_tag: object,
) -> None:
    """desk auto-stop --clear removes the shutdown tag."""
    mock_resolve.return_value = "i-abc123"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "main", "--clear"])
    assert result.exit_code == 0
    mock_clear_tag.assert_called_once_with("i-abc123", region=None, profile=None)
    assert "cleared" in result.output.lower()


@patch("desk_cli.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_workstation_not_found(mock_resolve: object) -> None:
    """desk auto-stop with unknown workstation shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk_cli.commands.up.get_default_private_key_path", return_value="/some/key")
@patch("desk_cli.commands.up.tab.tab_up")
@patch("desk_cli.commands.up.start_workstation")
@patch("desk_cli.commands.up.list_workstations")
@patch("desk_cli.commands.up.resolve_workstation")
def test_desk_up_sets_shutdown_tag_on_start(
    mock_resolve: object,
    mock_list: object,
    mock_start: object,
    mock_tab_up: object,
    _mock_key: object,
) -> None:
    """desk up sets shutdown tag when starting a stopped instance via SDK."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="main", state="stopped"),
    ]
    mock_start.return_value = ("i-stopped", "2026-02-07T20:00:00Z")

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "main"])
    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-stopped", shutdown_after="4h", region=None, profile=None)


def test_compute_shutdown_at() -> None:
    """compute_shutdown_at returns ISO 8601 timestamp in the future."""
    from datetime import datetime, timezone

    from desk.aws import compute_shutdown_at

    result = compute_shutdown_at(4.0)
    dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    # Should be roughly 4 hours from now (within 10 seconds tolerance)
    diff = (dt - now).total_seconds()
    assert 4 * 3600 - 10 < diff < 4 * 3600 + 10


def test_parse_duration_hours() -> None:
    """parse_duration handles hour formats."""
    from desk.aws import parse_duration

    assert parse_duration("4h") == 4.0
    assert parse_duration("4H") == 4.0
    assert parse_duration("0.5h") == 0.5


def test_parse_duration_minutes() -> None:
    """parse_duration handles minute formats."""
    from desk.aws import parse_duration

    assert parse_duration("30m") == 0.5
    assert parse_duration("90m") == 1.5
    assert parse_duration("15M") == 0.25


def test_parse_duration_combined() -> None:
    """parse_duration handles combined hour+minute formats."""
    from desk.aws import parse_duration

    assert parse_duration("2h30m") == 2.5
    assert parse_duration("1h15m") == 1.25


def test_parse_duration_bare_number() -> None:
    """parse_duration treats bare numbers as hours."""
    from desk.aws import parse_duration

    assert parse_duration("4") == 4.0
    assert parse_duration("0") == 0.0
    assert parse_duration("0.5") == 0.5


def test_parse_duration_invalid() -> None:
    """parse_duration raises ValueError on invalid input."""
    import pytest

    from desk.aws import parse_duration

    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration("abc")
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration("h")
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration("m")


# ── reap tests ──────────────────────────────────────────────────────


def test_desk_reap_help() -> None:
    """desk reap --help succeeds."""
    result = _run_desk("reap", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Stop all workstations past their auto-stop time" in output
    assert "--dry-run" in output


@patch("desk_cli.commands.reap.reap_overdue")
def test_desk_reap_stops_overdue(mock_reap: object) -> None:
    """desk reap stops instances whose shutdown time is in the past."""
    from desk.aws import Workstation

    mock_reap.return_value = [
        Workstation(instance_id="i-overdue", name="old", state="running", shutdown_at="2020-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    mock_reap.assert_called_once_with(region=None, profile=None, dry_run=False)
    assert "1 workstation(s) stopped" in result.output
    assert "i-overdue" in result.output


@patch("desk_cli.commands.reap.reap_overdue")
def test_desk_reap_dry_run(mock_reap: object) -> None:
    """desk reap --dry-run shows what would be stopped without stopping."""
    from desk.aws import Workstation

    mock_reap.return_value = [
        Workstation(instance_id="i-overdue", name="old", state="running", shutdown_at="2020-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap", "--dry-run"])
    assert result.exit_code == 0
    mock_reap.assert_called_once_with(region=None, profile=None, dry_run=True)
    assert "Would stop" in result.output
    assert "would be stopped" in result.output


@patch("desk_cli.commands.reap.reap_overdue")
def test_desk_reap_none_overdue(mock_reap: object) -> None:
    """desk reap with no overdue instances reports nothing to do."""
    mock_reap.return_value = []
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert "No overdue workstations" in result.output


@patch("desk_cli.commands.reap.reap_overdue")
def test_desk_reap_skips_no_tag(mock_reap: object) -> None:
    """desk reap skips instances without a shutdown tag."""
    mock_reap.return_value = []
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert "No overdue workstations" in result.output


@patch("desk_cli.commands.reap.reap_overdue")
def test_desk_reap_stops_multiple(mock_reap: object) -> None:
    """desk reap stops all overdue instances."""
    from desk.aws import Workstation

    mock_reap.return_value = [
        Workstation(instance_id="i-one", name="one", state="running", shutdown_at="2020-01-01T00:00:00Z"),
        Workstation(instance_id="i-two", name="two", state="running", shutdown_at="2020-06-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert "2 workstation(s) stopped" in result.output


# --- desk tab ---


def test_desk_tab_help() -> None:
    """desk tab --help shows subcommands."""
    result = _run_desk("tab", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "connect" in output
    assert "list" in output
    assert "create" in output
    assert "up" in output
    assert "close" in output
    assert "screen" in output.lower()


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_connect_calls_connection_with_screen(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab connect runs screen -ls via SSM, then builds SSH argv with screen -x."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("  12345.foo-tab\t(Detached)\n", "", "Success", 0)
    mock_get_argv.return_value = ["ssh", "-o", "ProxyCommand=...", "ubuntu@i-abc123", "screen -x '12345.foo-tab'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "connect", "main"])

    mock_get_argv.assert_called_once()
    call_kw = mock_get_argv.call_args[1]
    rc = call_kw["remote_command"]
    assert "screen -x" in rc
    assert "12345.foo-tab" in rc
    mock_execvp.assert_called_once_with("ssh", mock_get_argv.return_value)


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_connect_with_session_uses_session_id(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab connect with full session id passes that id to screen -x."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("  16691.desk-main\t(Detached)\n", "", "Success", 0)
    mock_get_argv.return_value = ["ssh", "ubuntu@i-abc123", "screen -x '16691.desk-main'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "connect", "main", "16691.desk-main"])

    assert result.exit_code == 127
    mock_get_argv.assert_called_once()
    call_kw = mock_get_argv.call_args[1]
    assert "screen -x" in call_kw["remote_command"]
    assert "16691.desk-main" in call_kw["remote_command"]


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.wait_for_ssm_ready")
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_list_no_session(
    _mock_settings: object,
    mock_resolve: object,
    mock_wait: object,
    mock_run_remote: object,
) -> None:
    """desk tab list shows message when no screen session exists."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("No Sockets found in /run/screen.", "", "Success", 0)

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "list", "main"])

    assert result.exit_code == 0
    assert "No screen sessions" in result.output
    assert "desk tab create main" in result.output


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_connect_fails_when_no_session(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
) -> None:
    """desk tab connect with no session raises when no screen sessions on workstation."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("No Sockets found in /run/screen.\n", "", "Success", 0)

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "connect", "main"])

    assert result.exit_code != 0
    assert "No screen session" in result.output
    assert "desk tab create main" in result.output


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_list_shows_session_and_windows(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """desk tab list shows one row per window with cwd and command."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = (
        "12345.desk-main\x01(Detached)\x010\x01bash\x01/home/ubuntu\x01bash\n"
        "12345.desk-main\x01(Detached)\x011\x01vim\x01/home/ubuntu/proj\x01vim\n",
        "",
        "Success",
        0,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "list", "main", "--windows"])

    assert result.exit_code == 0
    assert "desk-main" in result.output
    assert "0" in result.output and "bash" in result.output
    assert "1" in result.output and "vim" in result.output
    assert "/home/ubuntu/proj" in result.output
    assert "├─" in result.output and "└─" in result.output


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_list_default_no_winlist_call(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """desk tab list runs list-with-details script, one row per window."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = (
        "12345.desk-main\x01(Detached)\x010\x01bash\x01/home/ubuntu\x01bash\n",
        "",
        "Success",
        0,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "list", "main"])

    assert result.exit_code == 0
    assert "desk-main" in result.output
    assert "/home/ubuntu" in result.output
    assert "bash" in result.output
    assert "└─" in result.output or "├─" in result.output
    assert mock_run_remote.call_count == 1


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_list_tree_two_levels_one_line_per_window(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """Tab list shows 2-level tree; window info (idx, title, cwd, cmd) on one line.
    Matches structure from real screen -ls and screen -S ID -Q windows (e.g. 0*&$ bash  1-$ bash).
    """
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = (
        "1084.desk-main\x01(02/22/26 19:56:26)\t(Attached)\x010\x01bash\x01/home/ubuntu\x01bash\n"
        "1084.desk-main\x01(02/22/26 19:56:26)\t(Attached)\x011\x01bash\x01/home/ubuntu/proj\x01bash\n",
        "",
        "Success",
        0,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "list", "main"])

    assert result.exit_code == 0
    assert "1084.desk-main" in result.output
    assert "(Attached)" in result.output
    # Two levels only: session line, then window lines (no extra cwd:/cmd: lines)
    lines = result.output.strip().split("\n")
    assert any("1084.desk-main" in l and "Attached" in l for l in lines)
    window_lines = [l for l in lines if "├─" in l or "└─" in l]
    assert len(window_lines) == 2
    assert "cwd:" not in result.output and "cmd:" not in result.output
    assert "/home/ubuntu" in result.output and "/home/ubuntu/proj" in result.output


@patch("desk_cli.commands.tab.shutil.get_terminal_size")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_list_command_column_shows_full_desk_tab_list_main(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
    mock_get_terminal_size: object,
) -> None:
    """Command column gets enough space so '.tox/py/bin/desk tab list main' is not truncated."""
    mock_resolve.return_value = "i-abc123"
    mock_get_terminal_size.return_value = type("Size", (), {"columns": 80, "lines": 24})()
    cmd_text = ".tox/py/bin/desk tab list main"
    mock_run_remote.return_value = (
        f"1084.desk-main\x01(Attached)\x011\x01-\x01/home/ubuntu/tasks/fix-tabs/desk\x01{cmd_text}\n",
        "",
        "Success",
        0,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "list", "main"])

    assert result.exit_code == 0
    assert cmd_text in result.output, f"Expected {cmd_text!r} to appear in full in output"


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
@patch("desk_cli.commands.tab.new_session_name", return_value="abc123")
def test_desk_tab_create_success(
    mock_new_session: object,
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """desk tab create runs remote screen command and reports success with connect hint."""
    mock_resolve.return_value = "i-abc123"
    # First call: create success; second: screen -ls with session so we suggest full id
    mock_run_remote.side_effect = [
        ("", "", "Success", 0),
        ("  18426.abc123\t(Detached)\n", "", "Success", 0),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "create", "main"])

    assert result.exit_code == 0
    assert "Session created: abc123" in result.output
    assert "desk tab connect main 18426.abc123" in result.output
    assert mock_run_remote.call_count == 2
    # Without name, session uses short auto-generated name (hex, no prefix); first call is create
    create_cmd = mock_run_remote.call_args_list[0][0][1]
    match = re.search(r"screen -dmS ([a-f0-9]+) ", create_cmd)
    assert match, "session name should be short hex (no prefix)"
    assert match.group(1) == "abc123"


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_create_with_optional_name(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """desk tab create with NAME uses that as the session name (no prefix)."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.side_effect = [
        ("", "", "Success", 0),
        ("  9999.my-tab\t(Detached)\n", "", "Success", 0),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "create", "main", "my-tab"])

    assert result.exit_code == 0
    assert "Session created" in result.output
    create_cmd = mock_run_remote.call_args_list[0][0][1]
    assert "screen -dmS my-tab " in create_cmd


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_up_connects_when_session_exists(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab up with existing session connects without creating."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("  12345.foo-tab\t(Detached)\n", "", "Success", 0)
    mock_get_argv.return_value = ["ssh", "ubuntu@i-abc123", "screen -x '12345.foo-tab'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "up", "main"])

    assert result.exit_code == 127
    mock_get_argv.assert_called_once()
    call_kw = mock_get_argv.call_args[1]
    assert "12345.foo-tab" in call_kw["remote_command"]
    mock_execvp.assert_called_once_with("ssh", mock_get_argv.return_value)
    # Only screen -ls, no create
    assert mock_run_remote.call_count == 1


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
@patch("desk_cli.commands.tab.new_session_name", return_value="abc123")
def test_desk_tab_up_creates_then_connects_when_no_session(
    mock_new_session: object,
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab up with no sessions creates one then connects."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.side_effect = [
        ("No Sockets found in /run/screen.\n", "", "Success", 0),
        ("", "", "Success", 0),
        ("  18426.abc123\t(Detached)\n", "", "Success", 0),
    ]
    mock_get_argv.return_value = ["ssh", "ubuntu@i-abc123", "screen -x '18426.abc123'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "up", "main"])

    assert result.exit_code == 127
    assert mock_run_remote.call_count == 3  # screen -ls, create, screen -ls after create
    create_cmd = mock_run_remote.call_args_list[1][0][1]
    assert "screen -dmS abc123 " in create_cmd
    mock_get_argv.assert_called_once()
    assert "18426.abc123" in mock_get_argv.call_args[1]["remote_command"]


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_up_with_tab_name_connects_to_matching(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab up WORKSTATION TAB_NAME uses existing session when name matches."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.return_value = ("  9999.my-tab\t(Detached)\n", "", "Success", 0)
    mock_get_argv.return_value = ["ssh", "ubuntu@i-abc123", "screen -x '9999.my-tab'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "up", "main", "my-tab"])

    assert result.exit_code == 127
    mock_get_argv.assert_called_once()
    assert "9999.my-tab" in mock_get_argv.call_args[1]["remote_command"]
    assert mock_run_remote.call_count == 1


@patch("desk_cli.commands.tab.os.execvp")
@patch("desk_cli.commands.tab.get_connection_argv")
@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_up_with_tab_name_creates_when_no_match(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm_ready: object,
    mock_run_remote: object,
    mock_get_argv: object,
    mock_execvp: object,
) -> None:
    """desk tab up WORKSTATION TAB_NAME creates session when no matching name."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.side_effect = [
        ("  1111.other\t(Detached)\n", "", "Success", 0),
        ("", "", "Success", 0),
        ("  1111.other\t(Detached)\n  2222.my-tab\t(Detached)\n", "", "Success", 0),
    ]
    mock_get_argv.return_value = ["ssh", "ubuntu@i-abc123", "screen -x '2222.my-tab'"]
    mock_execvp.side_effect = OSError(2, "No such file")

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "up", "main", "my-tab"])

    assert result.exit_code == 127
    assert mock_run_remote.call_count == 3  # screen -ls, create, screen -ls after create
    create_cmd = mock_run_remote.call_args_list[1][0][1]
    assert "screen -dmS my-tab " in create_cmd
    assert "2222.my-tab" in mock_get_argv.call_args[1]["remote_command"]


def test_desk_tab_up_help() -> None:
    """desk tab up --help describes create-if-needed then connect."""
    result = _run_desk("tab", "up", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a screen session if needed" in output
    assert "WORKSTATION" in output
    assert "TAB_NAME" in output


@patch("desk_cli.commands.tab.run_remote_command")
@patch("desk_cli.commands.tab.is_ssm_ready", return_value=True)
@patch("desk_cli.commands.tab.resolve_workstation")
@patch("desk_cli.commands.tab.get_desk_settings", return_value=_TAB_CLI_SETTINGS)
def test_desk_tab_close_success(
    _mock_settings: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_run_remote: object,
) -> None:
    """desk tab close runs remote screen quit and reports success."""
    mock_resolve.return_value = "i-abc123"
    mock_run_remote.side_effect = [
        ("12345.foo-tab\t(Detached)\n", "", "Success", 0),
        ("", "", "Success", 0),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["tab", "close", "main", "foo-tab"])

    assert result.exit_code == 0
    assert "Session 12345.foo-tab closed" in result.output
    assert mock_run_remote.call_count == 2
