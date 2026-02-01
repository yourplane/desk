"""CLI tests."""

import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from desk.cli import main


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


def test_desk_create_help() -> None:
    """desk create --help succeeds and shows usage."""
    result = _run_desk("create", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Create a new workstation instance" in output
    assert "--name" in output


def test_desk_help() -> None:
    """desk --help succeeds."""
    result = _run_desk("--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Manage EC2 instances" in output
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


@patch("desk.commands.key.get_desk_keys_dir")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_success(mock_create: object, mock_keys_dir: object, tmp_path) -> None:
    """desk key create creates key and saves to desk keys dir."""
    keys_dir = str(tmp_path / "keys")
    mock_keys_dir.return_value = keys_dir
    mock_create.return_value = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"

    runner = CliRunner()
    result = runner.invoke(main, ["key", "create", "my-key"])

    assert result.exit_code == 0
    mock_create.assert_called_once_with(key_name="my-key", region=None, profile=None)
    key_path = f"{keys_dir}/my-key.pem"
    assert "Created key 'my-key'" in result.output
    assert key_path in result.output
    assert (tmp_path / "keys" / "my-key.pem").exists()


@patch("desk.commands.key.get_key_path")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_local_exists(mock_create: object, mock_key_path: object, tmp_path) -> None:
    """desk key create fails when local key file already exists."""
    existing = tmp_path / "my-key.pem"
    existing.write_text("existing")
    mock_key_path.return_value = str(existing)

    runner = CliRunner()
    result = runner.invoke(main, ["key", "create", "my-key"])

    assert result.exit_code != 0
    assert "already exists" in result.output
    mock_create.assert_not_called()


@patch("desk.commands.key.get_desk_keys_dir")
@patch("desk.commands.key.create_key_pair")
def test_desk_key_create_aws_duplicate(
    mock_create: object, mock_keys_dir: object, tmp_path
) -> None:
    """desk key create shows friendly error when key exists in AWS."""
    from botocore.exceptions import ClientError

    mock_keys_dir.return_value = str(tmp_path / "keys")
    mock_create.side_effect = ClientError(
        {"Error": {"Code": "InvalidKeyPair.Duplicate", "Message": "already exists"}},
        "CreateKeyPair",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["key", "create", "my-key"])

    assert result.exit_code != 0
    assert "already exists in AWS" in result.output


@patch("desk.commands.key.list_ec2_key_pairs")
@patch("desk.commands.key.list_local_keys")
def test_desk_key_list_empty(mock_local: object, mock_remote: object) -> None:
    """desk key list shows message when no keys."""
    mock_local.return_value = set()
    mock_remote.return_value = set()
    runner = CliRunner()
    result = runner.invoke(main, ["key", "list"])
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
    result = runner.invoke(main, ["key", "list"])
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
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "No workstations found" in result.output


def test_desk_connect_help() -> None:
    """desk connect --help succeeds."""
    result = _run_desk("connect", "--help")
    assert result.returncode == 0
    output = _output(result)
    assert "Connect to a workstation via SSH" in output
    assert "WORKSTATION" in output


@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
def test_desk_connect_resolves_and_execs_ssh(
    mock_resolve: object, mock_ssm: object, mock_execvp: object
) -> None:
    """desk connect resolves workstation and execs ssh with ProxyCommand."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.return_value = True  # SSM ready, skip wait
    # execvp replaces process - simulate it raising so we don't actually exec
    mock_execvp.side_effect = OSError(2, "No such file or directory")

    runner = CliRunner()
    result = runner.invoke(main, ["connect", "max"])

    mock_resolve.assert_called_once_with("max", region=None, profile=None)
    mock_execvp.assert_called_once()
    args = mock_execvp.call_args[0][1]
    assert args[0] == "ssh"
    assert "ProxyCommand=" in " ".join(args)
    assert "AWS-StartSSHSession" in " ".join(args)
    assert "ubuntu@i-abc123" in args


@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.os.execvp")
@patch("desk.commands.connect.is_ssm_ready")
@patch("desk.commands.connect.resolve_workstation")
def test_desk_connect_waits_for_ssm_then_connects(
    mock_resolve: object, mock_ssm: object, mock_execvp: object
) -> None:
    """desk connect waits when SSM not ready, then proceeds."""
    mock_resolve.return_value = "i-abc123"
    mock_ssm.side_effect = [False, False, True]  # Ready on 3rd check
    mock_execvp.side_effect = OSError(2, "No such file")
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "max", "--wait-timeout", "10"])
    assert mock_ssm.call_count == 3
    mock_execvp.assert_called_once()


@patch("desk.commands.connect.resolve_workstation")
def test_desk_connect_not_found(mock_resolve: object) -> None:
    """desk connect with unknown workstation shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'x' not found")
    runner = CliRunner()
    result = runner.invoke(main, ["connect", "x"])
    assert result.exit_code != 0
    assert "not found" in result.output


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
    result = runner.invoke(main, ["stop", "max"])
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
    result = runner.invoke(main, ["stop", "i-abc123"])
    assert result.exit_code == 0
    mock_resolve.assert_called_once_with("i-abc123", region=None, profile=None)
    mock_stop.assert_called_once_with("i-abc123", region=None, profile=None)


@patch("desk.commands.stop.resolve_workstation")
def test_desk_stop_not_found(mock_resolve: object) -> None:
    """desk stop with unknown name shows error."""
    mock_resolve.side_effect = ValueError("Workstation 'unknown' not found")
    runner = CliRunner()
    result = runner.invoke(main, ["stop", "unknown"])
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
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "i-abc123" in result.output
    assert "max" in result.output
    assert "running" in result.output
