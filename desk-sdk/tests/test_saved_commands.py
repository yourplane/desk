"""Tests for saved command storage and rendering."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from desk.saved_commands import (
    SavedCommand,
    SavedCommandParam,
    create_saved_command,
    delete_saved_command,
    extract_parameters,
    get_saved_command,
    list_saved_commands,
    render_script,
    update_saved_command,
)


SAMPLE_COMMANDS = [
    {
        "id": "abc123",
        "name": "Deploy",
        "script": "cd /app && git checkout {{branch}}",
        "description": "Deploy a branch",
        "parameters": [{"name": "branch", "default": "main"}],
    },
    {
        "id": "def456",
        "name": "Restart",
        "script": "systemctl restart {{service}}",
        "description": "",
        "parameters": [{"name": "service"}],
    },
]


def _make_s3_response(data: list) -> dict:
    body = MagicMock()
    body.read.return_value = json.dumps(data).encode()
    return {"Body": body}


def _no_such_key_error():
    from botocore.exceptions import ClientError

    return ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
        "GetObject",
    )


@pytest.fixture(autouse=True)
def _set_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESK_DATA_BUCKET", "test-bucket")


@patch("desk.saved_commands._s3_client")
def test_list_saved_commands(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    result = list_saved_commands()

    assert len(result) == 2
    assert result[0].id == "abc123"
    assert result[0].name == "Deploy"
    assert result[0].parameters[0].name == "branch"
    assert result[0].parameters[0].default == "main"
    assert result[1].id == "def456"
    assert result[1].parameters[0].default is None


@patch("desk.saved_commands._s3_client")
def test_list_saved_commands_empty_bucket(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.side_effect = _no_such_key_error()

    result = list_saved_commands()

    assert result == []


@patch("desk.saved_commands._s3_client")
def test_get_saved_command(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    cmd = get_saved_command("def456")

    assert cmd.name == "Restart"
    assert cmd.script == "systemctl restart {{service}}"


@patch("desk.saved_commands._s3_client")
def test_get_saved_command_not_found(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    with pytest.raises(ValueError, match="not found"):
        get_saved_command("nonexistent")


@patch("desk.saved_commands._s3_client")
def test_create_saved_command(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.side_effect = _no_such_key_error()

    cmd = create_saved_command(
        name="Test",
        script="echo {{msg}}",
        description="A test",
        parameters=[{"name": "msg", "default": "hello"}],
    )

    assert cmd.name == "Test"
    assert cmd.script == "echo {{msg}}"
    assert len(cmd.id) == 8
    assert cmd.parameters[0].name == "msg"
    assert cmd.parameters[0].default == "hello"

    s3.put_object.assert_called_once()
    put_call = s3.put_object.call_args
    body = json.loads(put_call.kwargs["Body"])
    assert len(body) == 1
    assert body[0]["name"] == "Test"


@patch("desk.saved_commands._s3_client")
def test_update_saved_command(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    cmd = update_saved_command("abc123", name="Deploy v2", description="Updated")

    assert cmd.name == "Deploy v2"
    assert cmd.description == "Updated"
    assert cmd.script == "cd /app && git checkout {{branch}}"
    s3.put_object.assert_called_once()


@patch("desk.saved_commands._s3_client")
def test_update_saved_command_not_found(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    with pytest.raises(ValueError, match="not found"):
        update_saved_command("nonexistent", name="X")


@patch("desk.saved_commands._s3_client")
def test_delete_saved_command(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    delete_saved_command("abc123")

    s3.put_object.assert_called_once()
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert len(body) == 1
    assert body[0]["id"] == "def456"


@patch("desk.saved_commands._s3_client")
def test_delete_saved_command_not_found(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response(SAMPLE_COMMANDS)

    with pytest.raises(ValueError, match="not found"):
        delete_saved_command("nonexistent")


def test_render_script_with_defaults() -> None:
    cmd = SavedCommand(
        id="x",
        name="Test",
        script="deploy {{branch}} to {{env}}",
        parameters=[
            SavedCommandParam(name="branch", default="main"),
            SavedCommandParam(name="env", default="staging"),
        ],
    )

    result = render_script(cmd, {})

    assert result == "deploy main to staging"


def test_render_script_with_overrides() -> None:
    cmd = SavedCommand(
        id="x",
        name="Test",
        script="deploy {{branch}} to {{env}}",
        parameters=[
            SavedCommandParam(name="branch", default="main"),
            SavedCommandParam(name="env", default="staging"),
        ],
    )

    result = render_script(cmd, {"branch": "feature-x", "env": "production"})

    assert result == "deploy feature-x to production"


def test_render_script_partial_overrides() -> None:
    cmd = SavedCommand(
        id="x",
        name="Test",
        script="deploy {{branch}} to {{env}}",
        parameters=[
            SavedCommandParam(name="branch", default="main"),
            SavedCommandParam(name="env", default="staging"),
        ],
    )

    result = render_script(cmd, {"branch": "hotfix"})

    assert result == "deploy hotfix to staging"


def test_render_script_missing_required() -> None:
    cmd = SavedCommand(
        id="x",
        name="Test",
        script="restart {{service}}",
        parameters=[SavedCommandParam(name="service")],
    )

    with pytest.raises(ValueError, match="Missing required parameter 'service'"):
        render_script(cmd, {})


def test_render_script_no_params() -> None:
    cmd = SavedCommand(
        id="x",
        name="Test",
        script="echo hello",
        parameters=[],
    )

    result = render_script(cmd, {})

    assert result == "echo hello"


def test_extract_parameters() -> None:
    assert extract_parameters("echo {{greeting}} {{name}}") == ["greeting", "name"]


def test_extract_parameters_deduplicates() -> None:
    assert extract_parameters("{{x}} + {{x}}") == ["x"]


def test_extract_parameters_none() -> None:
    assert extract_parameters("echo hello") == []


def test_extract_parameters_mixed() -> None:
    assert extract_parameters("{{a}} plain {{b}} more {{a}}") == ["a", "b"]
