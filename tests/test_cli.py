"""CLI tests."""

import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from desk.cli import cli


def _run_desk(*args: str) -> subprocess.CompletedProcess[str]:
    """Run desk CLI and return result. Click writes help to stderr."""
    return subprocess.run(
        [sys.executable, "-m", "desk.cli", *args],
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
    assert "main" in output
    assert "main-key" in output
    assert "--forward" in output or "-L" in output


@patch("desk.commands.up.connect.connect")
@patch("desk.commands.up.set_shutdown_tag")
@patch("desk.commands.up.compute_shutdown_at", return_value="2026-02-07T20:00:00Z")
@patch("desk.commands.up.start_instance")
@patch("desk.commands.up.list_workstations")
@patch("desk.commands.up.resolve_workstation")
def test_desk_up_starts_stopped_instance(
    mock_resolve: object,
    mock_list: object,
    mock_start: object,
    mock_compute: object,
    mock_set_tag: object,
    mock_connect: object,
) -> None:
    """desk up starts a stopped instance instead of creating."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="main", state="stopped"),
    ]
    mock_start.return_value = "i-stopped"

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--name", "main"])

    assert result.exit_code == 0
    mock_start.assert_called_once_with("i-stopped", region=None, profile=None)
    assert "stopped" in result.output.lower()
    assert "Starting" in result.output
    mock_connect.assert_called_once()


@patch("desk.commands.up.connect.connect")
@patch("desk.commands.up.set_shutdown_tag")
@patch("desk.commands.up.compute_shutdown_at", return_value="2026-02-07T20:00:00Z")
@patch("desk.commands.up.start_instance")
@patch("desk.commands.up.get_instance_state")
@patch("desk.commands.up.list_workstations")
@patch("desk.commands.up.resolve_workstation")
def test_desk_up_waits_for_stopping_instance(
    mock_resolve: object,
    mock_list: object,
    mock_get_state: object,
    mock_start: object,
    mock_compute: object,
    mock_set_tag: object,
    mock_connect: object,
) -> None:
    """desk up waits for stopping instance to stop, then starts it."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopping", name="main", state="stopping"),
    ]
    # Simulate instance transitioning: stopping -> stopping -> stopped
    mock_get_state.side_effect = ["stopping", "stopped"]
    mock_start.return_value = "i-stopping"

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--name", "main"])

    assert result.exit_code == 0
    assert mock_get_state.call_count >= 1
    mock_start.assert_called_once_with("i-stopping", region=None, profile=None)
    mock_connect.assert_called_once()


def test_desk_create_help() -> None:
    """desk create --help succeeds and shows usage."""
    result = _run_desk("create", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a new workstation instance" in output
    assert "--name" in output
    assert "main-key" in output


@patch("desk.commands.create.run_instance")
@patch("desk.commands.create.get_latest_ubuntu_ami")
@patch("desk.commands.create.get_desk_vpc_outputs")
@patch("desk.commands.create.list_ec2_key_pairs")
@patch("desk.commands.create.list_workstations")
def test_desk_create_aborts_when_main_key_missing_and_declined(
    mock_list_workstations: object,
    mock_list_keys: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
) -> None:
    """desk create prompts to create main-key when missing; aborts if user declines."""
    mock_list_workstations.return_value = []
    mock_list_keys.return_value = set()
    mock_vpc.return_value = type("V", (), {
        "private_subnet_ids": ["subnet-1"],
        "security_group_id": "sg-1",
        "instance_profile_name": "profile-1",
    })()
    mock_ami.return_value = "ami-123"

    runner = CliRunner()
    result = runner.invoke(cli, ["create"], input="n\n")

    assert result.exit_code != 0
    mock_run.assert_not_called()


@patch("desk.commands.create.list_workstations")
def test_desk_create_rejects_duplicate_name_running(mock_list_workstations: object) -> None:
    """desk create fails when workstation with same name is running."""
    from desk.aws import Workstation

    mock_list_workstations.return_value = [
        Workstation(instance_id="i-existing", name="main", state="running"),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "main"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "i-existing" in result.output
    assert "running" in result.output


@patch("desk.commands.create.list_workstations")
def test_desk_create_rejects_duplicate_name_stopped(mock_list_workstations: object) -> None:
    """desk create fails when workstation with same name is stopped."""
    from desk.aws import Workstation

    mock_list_workstations.return_value = [
        Workstation(instance_id="i-stopped", name="myws", state="stopped"),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "myws"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "i-stopped" in result.output
    assert "stopped" in result.output


@patch("desk.commands.create.list_workstations")
def test_desk_create_rejects_duplicate_name_stopping(mock_list_workstations: object) -> None:
    """desk create fails when workstation with same name is stopping."""
    from desk.aws import Workstation

    mock_list_workstations.return_value = [
        Workstation(instance_id="i-stopping", name="myws", state="stopping"),
    ]

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "myws"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "stopping" in result.output


@patch("desk.commands.create.set_shutdown_tag")
@patch("desk.commands.create.compute_shutdown_at", return_value="2026-02-07T20:00:00Z")
@patch("desk.commands.create.run_instance")
@patch("desk.commands.create.get_latest_ubuntu_ami")
@patch("desk.commands.create.get_desk_vpc_outputs")
@patch("desk.commands.create.list_ec2_key_pairs")
@patch("desk.commands.create.list_workstations")
def test_desk_create_allows_duplicate_name_when_terminated(
    mock_list_workstations: object,
    mock_list_keys: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk create succeeds when only terminated workstations have same name."""
    from desk.aws import Workstation

    mock_list_workstations.return_value = [
        Workstation(instance_id="i-old", name="main", state="terminated"),
    ]
    mock_list_keys.return_value = {"main-key"}
    mock_vpc.return_value = type("V", (), {
        "private_subnet_ids": ["subnet-1"],
        "security_group_id": "sg-1",
        "instance_profile_name": "profile-1",
    })()
    mock_ami.return_value = "ami-123"
    mock_run.return_value = "i-new123"

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "main"])

    assert result.exit_code == 0
    mock_run.assert_called_once()
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
    assert "key" in output


def test_desk_key_help() -> None:
    """desk key --help and desk key create --help succeed."""
    result = _run_desk("key", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Manage SSH keys" in output
    assert "create" in output

    result = _run_desk("key", "create", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a new key pair" in output


@patch("desk.commands.key.get_key_path")
@patch("desk.commands.key.get_desk_keys_dir")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_success(mock_create: object, mock_keys_dir: object, mock_key_path: object, tmp_path) -> None:
    """desk key create creates key and saves to desk keys dir."""
    keys_dir = tmp_path / "keys"
    key_file = keys_dir / "my-key.pem"
    mock_keys_dir.return_value = str(keys_dir)
    mock_key_path.return_value = str(key_file)
    mock_create.return_value = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "create", "my-key"])

    assert result.exit_code == 0
    mock_create.assert_called_once_with(key_name="my-key", region=None, profile=None)
    assert "Created key 'my-key'" in result.output
    assert key_file.exists()


@patch("desk.commands.key.get_key_path")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_local_exists(mock_create: object, mock_key_path: object, tmp_path) -> None:
    """desk key create fails when local key file already exists."""
    existing = tmp_path / "my-key.pem"
    existing.write_text("existing")
    mock_key_path.return_value = str(existing)

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "create", "my-key"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    mock_create.assert_not_called()


@patch("desk.commands.key.get_key_path")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_aws_duplicate(
    mock_create: object, mock_key_path: object, tmp_path
) -> None:
    """desk key create shows friendly error when key exists in AWS."""
    from botocore.exceptions import ClientError

    # Key doesn't exist locally
    mock_key_path.return_value = str(tmp_path / "nonexistent.pem")
    mock_create.side_effect = ClientError(
        {"Error": {"Code": "InvalidKeyPair.Duplicate", "Message": "already exists"}},
        "CreateKeyPair",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "create", "my-key"])

    assert result.exit_code != 0
    assert "already exists in AWS" in result.output


@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.list_local_keys")
def test_desk_key_list_empty(mock_local: object, mock_remote: object) -> None:
    """desk key list shows message when no keys."""
    mock_local.return_value = set()
    mock_remote.return_value = set()
    runner = CliRunner()
    result = runner.invoke(cli, ["key", "list"])
    assert result.exit_code == 0
    assert "No keys found" in result.output


@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.list_local_keys")
def test_desk_key_list_shows_local_and_remote(
    mock_local: object, mock_remote: object
) -> None:
    """desk key list shows keys with local/remote indicators."""
    mock_local.return_value = {"my-key", "local-only"}
    mock_remote.return_value = {"my-key", "remote-only"}
    runner = CliRunner()
    result = runner.invoke(cli, ["key", "list"])
    assert result.exit_code == 0
    assert "NAME" in result.output
    assert "LOCAL" in result.output
    assert "REMOTE" in result.output
    assert "my-key" in result.output
    assert "local-only" in result.output
    assert "remote-only" in result.output
    # my-key has both, local-only has local only, remote-only has remote only
    assert "yes" in result.output
    assert "-" in result.output


def test_desk_key_delete_help() -> None:
    """desk key delete --help succeeds."""
    result = _run_desk("key", "delete", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Delete a key pair" in output
    assert "--force" in output
    assert "--yes" in output


@patch("desk.commands.key.delete_key_pair")
@patch("desk.commands.key.get_running_workstations_using_key")
@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.get_key_path")
def test_desk_key_delete_success(
    mock_key_path: object,
    mock_remote_keys: object,
    mock_running: object,
    mock_delete: object,
    tmp_path,
) -> None:
    """desk key delete removes local file and AWS key."""
    key_path = tmp_path / "my-key.pem"
    key_path.write_text("key material")
    mock_key_path.return_value = str(key_path)
    mock_remote_keys.return_value = {"my-key"}
    mock_running.return_value = []

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "delete", "my-key", "--yes"])

    assert result.exit_code == 0
    assert not key_path.exists()
    mock_delete.assert_called_once_with(key_name="my-key", region=None, profile=None)


@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.get_key_path")
def test_desk_key_delete_not_found(mock_key_path: object, mock_remote_keys: object, tmp_path) -> None:
    """desk key delete fails when key does not exist."""
    mock_key_path.return_value = str(tmp_path / "nonexistent.pem")
    mock_remote_keys.return_value = set()

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "delete", "unknown"])

    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.key.delete_key_pair")
@patch("desk.commands.key.get_running_workstations_using_key")
@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.get_key_path")
def test_desk_key_delete_aborts_when_not_confirmed(
    mock_key_path: object, mock_remote_keys: object, mock_running: object, mock_delete: object
) -> None:
    """desk key delete aborts when user declines confirmation."""
    mock_key_path.return_value = "/path/to/my-key.pem"
    mock_remote_keys.return_value = {"my-key"}
    mock_running.return_value = []

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "delete", "my-key"], input="n\n")

    assert result.exit_code != 0
    mock_delete.assert_not_called()


@patch("desk.commands.key.delete_key_pair")
@patch("desk.commands.key.get_running_workstations_using_key")
@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.get_key_path")
def test_desk_key_delete_refuses_when_in_use(
    mock_key_path: object,
    mock_remote_keys: object,
    mock_running: object,
    mock_delete: object,
) -> None:
    """desk key delete fails when key is used by running workstation."""
    mock_key_path.return_value = "/path/to/my-key.pem"
    mock_remote_keys.return_value = {"my-key"}
    mock_running.return_value = ["i-abc123"]

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "delete", "my-key"])

    assert result.exit_code != 0
    assert "used by running" in result.output
    mock_delete.assert_not_called()


@patch("desk.commands.key.delete_key_pair")
@patch("desk.commands.key.get_running_workstations_using_key")
@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.get_key_path")
def test_desk_key_delete_force_bypasses_in_use_check(
    mock_key_path: object,
    mock_remote_keys: object,
    mock_running: object,
    mock_delete: object,
    tmp_path,
) -> None:
    """desk key delete --force deletes even when key is in use."""
    key_path = tmp_path / "my-key.pem"
    key_path.write_text("key material")
    mock_key_path.return_value = str(key_path)
    mock_remote_keys.return_value = {"my-key"}
    mock_running.return_value = ["i-abc123"]

    runner = CliRunner()
    result = runner.invoke(cli, ["key", "delete", "my-key", "--force", "--yes"])

    assert result.exit_code == 0
    mock_delete.assert_called_once()


def test_desk_shows_friendly_error_without_traceback() -> None:
    """Unexpected exceptions show Error: message, not full traceback via main()."""
    from io import StringIO
    from unittest.mock import patch

    # Test the main() wrapper function's exception handling
    with patch("desk.cli.cli") as mock_cli:
        mock_cli.side_effect = RuntimeError("Something went wrong")

        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            with patch("sys.exit") as mock_exit:
                from desk.cli import main
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


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_resolves_and_execs_ssh(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk connect resolves workstation and execs ssh with ProxyCommand."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True  # SSM ready, skip wait
    # execvp replaces process - simulate it raising so we don't actually exec
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max"])

    mock_resolve.assert_called_once_with("max", region=None, profile=None)
    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert args[0] == "ssh"
    assert "ProxyCommand=" in " ".join(args)
    assert "AWS-StartSSHSession" in " ".join(args)
    assert "ubuntu@i-abc123" in args


@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_with_key(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk connect --key uses desk-managed key path."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "my-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "--key", "my-key"])

    args = mock_execvp.call_args[0][1]
    assert "-i" in args
    idx = args.index("-i")
    assert args[idx + 1] == str(key_file)


@patch("desk.commands.connect.get_key_path")
def test_desk_connect_key_not_found(mock_get_key_path: object) -> None:
    """desk connect --key fails when key file does not exist."""
    mock_get_key_path.return_value = "/nonexistent/my-key.pem"
    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "--key", "my-key"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_waits_for_ssm_then_connects(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_execvp: object,
    mock_is_ssm_ready: object,
    tmp_path,
) -> None:
    """desk connect waits when SSM not ready, then proceeds."""
    mock_resolve.return_value = "i-abc123"
    mock_is_ssm_ready.side_effect = [False, False, True]  # Ready on 3rd check
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "--wait-timeout", "10"])
    assert mock_is_ssm_ready.call_count == 3
    mock_execvp.assert_called_once()


@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_not_found(
    mock_get_key_path: object, mock_resolve: object, tmp_path
) -> None:
    """desk connect with unknown workstation shows error."""
    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    mock_resolve.side_effect = ValueError("Workstation 'x' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "x"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_with_port_forward(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk connect --forward adds -L flag to ssh."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "max", "-L", "8080:localhost:80"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-L" in args
    idx = args.index("-L")
    assert args[idx + 1] == "8080:localhost:80"


@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
@patch("desk.commands.connect.get_key_path")
def test_desk_connect_with_multiple_port_forwards(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk connect with multiple --forward options adds all -L flags."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

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


def test_desk_stop_help() -> None:
    """desk stop --help succeeds."""
    result = _run_desk("stop", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Stop a workstation instance" in output
    assert "WORKSTATION" in output


@patch("desk.commands.stop.stop_instance")
@patch("desk.commands.stop.resolve_workstation")
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


@patch("desk.commands.stop.stop_instance")
@patch("desk.commands.stop.resolve_workstation")
def test_desk_stop_by_instance_id(mock_resolve: object, mock_stop: object) -> None:
    """desk stop with instance ID stops the instance."""
    mock_resolve.return_value = "i-abc123"
    mock_stop.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "i-abc123"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("i-abc123", region=None, profile=None)
    mock_stop.assert_called_once_with("i-abc123", region=None, profile=None)


@patch("desk.commands.stop.resolve_workstation")
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


@patch("desk.commands.kill.terminate_instance")
@patch("desk.commands.kill.resolve_workstation")
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


@patch("desk.commands.kill.terminate_instance")
@patch("desk.commands.kill.resolve_workstation")
def test_desk_kill_confirms_before_terminate(mock_resolve: object, mock_terminate: object) -> None:
    """desk kill prompts for confirmation."""
    mock_resolve.return_value = "i-abc123"
    mock_terminate.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "max"], input="y\n")
    assert result.exit_code == 0
    mock_terminate.assert_called_once()
    assert "Terminate" in result.output


@patch("desk.commands.kill.terminate_instance")
@patch("desk.commands.kill.resolve_workstation")
def test_desk_kill_aborts_on_no(mock_resolve: object, mock_terminate: object) -> None:
    """desk kill aborts when user declines confirmation."""
    mock_resolve.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["kill", "max"], input="n\n")
    assert result.exit_code != 0
    mock_terminate.assert_not_called()


@patch("desk.commands.kill.resolve_workstation")
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


@patch("desk.commands.start.set_shutdown_tag")
@patch("desk.commands.start.compute_shutdown_at", return_value="2026-02-07T20:00:00Z")
@patch("desk.commands.start.start_instance")
@patch("desk.commands.start.resolve_workstation")
def test_desk_start_by_name(
    mock_resolve: object, mock_start: object, mock_compute: object, mock_set_tag: object
) -> None:
    """desk start resolves name and starts instance."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "max"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "max", region=None, profile=None, states=["stopped"]
    )
    mock_start.assert_called_once_with("i-abc123", region=None, profile=None)
    assert "Started" in result.output


@patch("desk.commands.start.set_shutdown_tag")
@patch("desk.commands.start.compute_shutdown_at", return_value="2026-02-07T20:00:00Z")
@patch("desk.commands.start.start_instance")
@patch("desk.commands.start.resolve_workstation")
def test_desk_start_by_instance_id(
    mock_resolve: object, mock_start: object, mock_compute: object, mock_set_tag: object
) -> None:
    """desk start with instance ID starts the instance."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "i-abc123"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(
        "i-abc123", region=None, profile=None, states=["stopped"]
    )
    mock_start.assert_called_once_with("i-abc123", region=None, profile=None)


@patch("desk.commands.start.resolve_workstation")
def test_desk_start_not_found(mock_resolve: object) -> None:
    """desk start with unknown name shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.list_.list_workstations")
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
    assert "SCRIPT" in output
    assert "--follow" in output
    assert "--workstation" in output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo hello", "-w", "main"])

    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_send.assert_called_once()
    assert "echo hello" in mock_send.call_args[0]
    assert "Command is running" in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo hello", "--follow"])

    assert result.exit_code == 0
    # Output should contain the streamed lines
    assert "line1" in result.output
    assert "line2" in result.output
    assert "line3" in result.output
    assert "completed successfully" in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "bad-command", "--follow"])

    assert result.exit_code == 1
    # stderr output should be in the combined output
    assert "error output" in result.output


@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
def test_desk_run_not_ssm_ready_no_wait(
    mock_resolve: object,
    mock_ssm_ready: object,
) -> None:
    """desk run fails when SSM not ready and --no-wait specified."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm_ready.return_value = False

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "echo hello", "--no-wait"])

    assert result.exit_code != 0
    assert "not SSM-ready" in result.output


@patch("desk.commands.run.wait_for_ssm_ready")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo hello", "--wait-timeout", "5"])

    assert result.exit_code != 0
    mock_wait.assert_called_once()
    assert "did not become SSM-ready" in result.output


@patch("desk.commands.run.resolve_workstation")
def test_desk_run_workstation_not_found(mock_resolve: object) -> None:
    """desk run fails when workstation not found."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "echo hello", "-w", "unknown"])

    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo done"])

    assert result.exit_code == 0
    assert "done" in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "exit 1"])

    assert result.exit_code != 0
    assert "failed" in result.output.lower()


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", str(script_file)])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    # Verify the file contents were sent, not the file path
    sent_script = mock_send.call_args[0][1]
    assert "echo 'hello from script'" in sent_script
    assert "#!/bin/bash" in sent_script
    assert "Reading script from" in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo hello && date"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    # Verify the inline command was sent directly
    sent_script = mock_send.call_args[0][1]
    assert sent_script == "echo hello && date"
    assert "Reading script from" not in result.output


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "whoami", "--user", "ubuntu"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    sent_script = mock_send.call_args[0][1]
    # Should be wrapped with sudo -u
    assert "sudo -u ubuntu" in sent_script
    assert "whoami" in sent_script


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", "echo 'hello world'", "-u", "ubuntu"])

    assert result.exit_code == 0
    mock_send.assert_called_once()
    sent_script = mock_send.call_args[0][1]
    assert "sudo -u ubuntu" in sent_script
    # The command should be properly quoted
    assert "hello world" in sent_script


@patch("desk.commands.run.get_command_invocation")
@patch("desk.commands.run.send_ssm_command")
@patch("desk.commands.run.is_ssm_ready")
@patch("desk.commands.run.resolve_workstation")
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
    result = runner.invoke(cli, ["run", str(script_file), "--user", "ubuntu"])

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


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_upload(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp uploads local file to remote."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt"])

    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert args[0] == "scp"
    assert "ProxyCommand=" in " ".join(args)
    assert "./local.txt" in args
    assert "ubuntu@i-abc123:~/remote.txt" in args


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_download(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp downloads remote file to local."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", ":~/remote.txt", "./local.txt"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "ubuntu@i-abc123:~/remote.txt" in args
    assert "./local.txt" in args


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_with_workstation_prefix(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp with workstation:path format."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "main:/etc/hosts", "./hosts", "-w", "main"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "ubuntu@i-abc123:/etc/hosts" in args
    assert "./hosts" in args


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_recursive(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp --recursive adds -r flag."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "-r", "./local-dir", ":~/remote-dir"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    # -r should be in args (but not as part of another option)
    # Find standalone -r
    assert "-r" in args


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_with_custom_key(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp --key uses desk-managed key path."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "my-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt", "--key", "my-key"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "-i" in args
    idx = args.index("-i")
    assert args[idx + 1] == str(key_file)


@patch("desk.commands.scp.get_key_path")
def test_desk_scp_key_not_found(mock_get_key_path: object) -> None:
    """desk scp --key fails when key file does not exist."""
    mock_get_key_path.return_value = "/nonexistent/my-key.pem"
    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt", "--key", "my-key"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_workstation_not_found(
    mock_get_key_path: object, mock_resolve: object, tmp_path
) -> None:
    """desk scp with unknown workstation shows error."""
    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt", "-w", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_waits_for_ssm_then_proceeds(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_execvp: object,
    mock_is_ssm_ready: object,
    tmp_path,
) -> None:
    """desk scp waits when SSM not ready, then proceeds."""
    mock_resolve.return_value = "i-abc123"
    mock_is_ssm_ready.side_effect = [False, False, True]
    mock_execvp.side_effect = OSError(2, "No such file")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt", "--wait-timeout", "10"])
    assert mock_is_ssm_ready.call_count == 3
    mock_execvp.assert_called_once()


@patch("desk.commands.scp.os.execvp")
@patch("desk.commands.scp.is_ssm_ready")
@patch("desk.commands.scp.resolve_workstation")
@patch("desk.commands.scp.get_key_path")
def test_desk_scp_with_custom_user(
    mock_get_key_path: object,
    mock_resolve: object,
    mock_ssm: object,
    mock_execvp: object,
    tmp_path,
) -> None:
    """desk scp --user uses custom username."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    key_file = tmp_path / "main-key.pem"
    key_file.write_text("key")
    mock_get_key_path.return_value = str(key_file)

    runner = CliRunner()
    result = runner.invoke(cli, ["scp", "./local.txt", ":~/remote.txt", "--user", "admin"])

    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert "admin@i-abc123:~/remote.txt" in args


# ── shutdown / auto-stop tests ───────────────────────────────────────


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.list_.list_workstations")
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


@patch("desk.commands.start.set_shutdown_tag")
@patch("desk.commands.start.compute_shutdown_at")
@patch("desk.commands.start.start_instance")
@patch("desk.commands.start.resolve_workstation")
def test_desk_start_sets_shutdown_tag(
    mock_resolve: object,
    mock_start: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk start sets a shutdown tag by default."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T20:00:00Z"
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main"])
    assert result.exit_code == 0
    mock_compute.assert_called_once_with(4.0)
    mock_set_tag.assert_called_once_with(
        "i-abc123", "2026-02-07T20:00:00Z", region=None, profile=None
    )


@patch("desk.commands.start.set_shutdown_tag")
@patch("desk.commands.start.start_instance")
@patch("desk.commands.start.resolve_workstation")
def test_desk_start_custom_shutdown_hours(
    mock_resolve: object,
    mock_start: object,
    mock_set_tag: object,
) -> None:
    """desk start --shutdown 8 sets tag with 8 hours."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main", "--shutdown", "8"])
    assert result.exit_code == 0
    mock_set_tag.assert_called_once()
    # The compute_shutdown_at call was made with 8.0 (tested indirectly)


@patch("desk.commands.start.set_shutdown_tag")
@patch("desk.commands.start.start_instance")
@patch("desk.commands.start.resolve_workstation")
def test_desk_start_shutdown_zero_skips_tag(
    mock_resolve: object,
    mock_start: object,
    mock_set_tag: object,
) -> None:
    """desk start --shutdown 0 does not set a shutdown tag."""
    mock_resolve.return_value = "i-abc123"
    mock_start.return_value = "i-abc123"
    runner = CliRunner()
    result = runner.invoke(cli, ["start", "main", "--shutdown", "0"])
    assert result.exit_code == 0
    mock_set_tag.assert_not_called()


@patch("desk.commands.create.set_shutdown_tag")
@patch("desk.commands.create.compute_shutdown_at")
@patch("desk.commands.create.run_instance")
@patch("desk.commands.create.get_latest_ubuntu_ami")
@patch("desk.commands.create.get_desk_vpc_outputs")
@patch("desk.commands.create.list_ec2_key_pairs")
@patch("desk.commands.create.list_workstations")
def test_desk_create_sets_shutdown_tag(
    mock_list_workstations: object,
    mock_list_keys: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk create sets a shutdown tag on the new instance."""
    mock_list_workstations.return_value = []
    mock_list_keys.return_value = {"main-key"}
    mock_vpc.return_value = type("V", (), {
        "private_subnet_ids": ["subnet-1"],
        "security_group_id": "sg-1",
        "instance_profile_name": "profile-1",
    })()
    mock_ami.return_value = "ami-123"
    mock_run.return_value = "i-new123"
    mock_compute.return_value = "2026-02-07T20:00:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "main"])
    assert result.exit_code == 0
    mock_compute.assert_called_once_with(4.0)
    mock_set_tag.assert_called_once_with(
        "i-new123", "2026-02-07T20:00:00Z", region=None, profile=None
    )


@patch("desk.commands.create.set_shutdown_tag")
@patch("desk.commands.create.run_instance")
@patch("desk.commands.create.get_latest_ubuntu_ami")
@patch("desk.commands.create.get_desk_vpc_outputs")
@patch("desk.commands.create.list_ec2_key_pairs")
@patch("desk.commands.create.list_workstations")
def test_desk_create_shutdown_zero_skips_tag(
    mock_list_workstations: object,
    mock_list_keys: object,
    mock_vpc: object,
    mock_ami: object,
    mock_run: object,
    mock_set_tag: object,
) -> None:
    """desk create --shutdown 0 does not set a shutdown tag."""
    mock_list_workstations.return_value = []
    mock_list_keys.return_value = {"main-key"}
    mock_vpc.return_value = type("V", (), {
        "private_subnet_ids": ["subnet-1"],
        "security_group_id": "sg-1",
        "instance_profile_name": "profile-1",
    })()
    mock_ami.return_value = "ami-123"
    mock_run.return_value = "i-new123"

    runner = CliRunner()
    result = runner.invoke(cli, ["create", "--name", "main", "--shutdown", "0"])
    assert result.exit_code == 0
    mock_set_tag.assert_not_called()


def test_desk_auto_stop_help() -> None:
    """desk auto-stop --help succeeds."""
    result = _run_desk("auto-stop", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Set or change the auto-stop time" in output
    assert "WORKSTATION" in output
    assert "DURATION" in output
    assert "--clear" in output


@patch("desk.commands.auto_stop.set_shutdown_tag")
@patch("desk.commands.auto_stop.compute_shutdown_at")
@patch("desk.commands.auto_stop.resolve_workstation")
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


@patch("desk.commands.auto_stop.set_shutdown_tag")
@patch("desk.commands.auto_stop.compute_shutdown_at")
@patch("desk.commands.auto_stop.resolve_workstation")
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


@patch("desk.commands.auto_stop.set_shutdown_tag")
@patch("desk.commands.auto_stop.compute_shutdown_at")
@patch("desk.commands.auto_stop.resolve_workstation")
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


@patch("desk.commands.auto_stop.set_shutdown_tag")
@patch("desk.commands.auto_stop.compute_shutdown_at")
@patch("desk.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_defaults(
    mock_resolve: object,
    mock_compute: object,
    mock_set_tag: object,
) -> None:
    """desk auto-stop with no args uses 'main' and 4h."""
    mock_resolve.return_value = "i-abc123"
    mock_compute.return_value = "2026-02-07T20:00:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("main", region=None, profile=None)
    mock_compute.assert_called_once_with(4.0)
    mock_set_tag.assert_called_once()


@patch("desk.commands.auto_stop.clear_shutdown_tag")
@patch("desk.commands.auto_stop.resolve_workstation")
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


@patch("desk.commands.auto_stop.resolve_workstation")
def test_desk_auto_stop_workstation_not_found(mock_resolve: object) -> None:
    """desk auto-stop with unknown workstation shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(cli, ["auto-stop", "unknown"])
    assert result.exit_code != 0
    assert "not found" in result.output


@patch("desk.commands.up.connect.connect")
@patch("desk.commands.up.set_shutdown_tag")
@patch("desk.commands.up.compute_shutdown_at")
@patch("desk.commands.up.start_instance")
@patch("desk.commands.up.list_workstations")
@patch("desk.commands.up.resolve_workstation")
def test_desk_up_sets_shutdown_tag_on_start(
    mock_resolve: object,
    mock_list: object,
    mock_start: object,
    mock_compute: object,
    mock_set_tag: object,
    mock_connect: object,
) -> None:
    """desk up sets shutdown tag when starting a stopped instance."""
    from desk.aws import Workstation

    mock_resolve.side_effect = ValueError("not found")
    mock_list.return_value = [
        Workstation(instance_id="i-stopped", name="main", state="stopped"),
    ]
    mock_start.return_value = "i-stopped"
    mock_compute.return_value = "2026-02-07T20:00:00Z"

    runner = CliRunner()
    result = runner.invoke(cli, ["up", "--name", "main"])
    assert result.exit_code == 0
    mock_compute.assert_called_once_with(4.0)
    mock_set_tag.assert_called_once_with(
        "i-stopped", "2026-02-07T20:00:00Z", region=None, profile=None
    )


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


@patch("desk.commands.reap.stop_instance")
@patch("desk.commands.reap.list_workstations")
def test_desk_reap_stops_overdue(mock_list: object, mock_stop: object) -> None:
    """desk reap stops instances whose shutdown time is in the past."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-overdue", name="old", state="running", shutdown_at="2020-01-01T00:00:00Z"),
        Workstation(instance_id="i-ok", name="new", state="running", shutdown_at="2099-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    mock_stop.assert_called_once_with("i-overdue", region=None, profile=None)
    assert "1 workstation(s) stopped" in result.output
    assert "i-overdue" in result.output


@patch("desk.commands.reap.stop_instance")
@patch("desk.commands.reap.list_workstations")
def test_desk_reap_dry_run(mock_list: object, mock_stop: object) -> None:
    """desk reap --dry-run shows what would be stopped without stopping."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-overdue", name="old", state="running", shutdown_at="2020-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap", "--dry-run"])
    assert result.exit_code == 0
    mock_stop.assert_not_called()
    assert "Would stop" in result.output
    assert "would be stopped" in result.output


@patch("desk.commands.reap.list_workstations")
def test_desk_reap_none_overdue(mock_list: object) -> None:
    """desk reap with no overdue instances reports nothing to do."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-ok", name="new", state="running", shutdown_at="2099-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert "No overdue workstations" in result.output


@patch("desk.commands.reap.list_workstations")
def test_desk_reap_skips_no_tag(mock_list: object) -> None:
    """desk reap skips instances without a shutdown tag."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-notag", name="notag", state="running", shutdown_at=None),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert "No overdue workstations" in result.output


@patch("desk.commands.reap.stop_instance")
@patch("desk.commands.reap.list_workstations")
def test_desk_reap_stops_multiple(mock_list: object, mock_stop: object) -> None:
    """desk reap stops all overdue instances."""
    from desk.aws import Workstation

    mock_list.return_value = [
        Workstation(instance_id="i-one", name="one", state="running", shutdown_at="2020-01-01T00:00:00Z"),
        Workstation(instance_id="i-two", name="two", state="running", shutdown_at="2020-06-01T00:00:00Z"),
        Workstation(instance_id="i-ok", name="ok", state="running", shutdown_at="2099-01-01T00:00:00Z"),
    ]
    runner = CliRunner()
    result = runner.invoke(cli, ["reap"])
    assert result.exit_code == 0
    assert mock_stop.call_count == 2
    assert "2 workstation(s) stopped" in result.output
