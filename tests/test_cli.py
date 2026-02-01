"""CLI tests."""

import subprocess
import sys


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


def test_desk_version() -> None:
    """desk --version prints version."""
    result = _run_desk("--version")
    assert result.returncode == 0
    output = _output(result)
    assert "desk, version 0.1.0" in output
