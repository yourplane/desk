"""Tests for web route port storage."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from desk.web_routes import (
    add_port,
    get_ports,
    list_all_web_routes,
    remove_port,
)


def _make_s3_response_dict(data: dict) -> dict:
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


@patch("desk.web_routes._s3_client")
def test_list_all_empty(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.side_effect = _no_such_key_error()

    assert list_all_web_routes() == {}


@patch("desk.web_routes._s3_client")
def test_list_all_reads_map(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"ws-a": [80, 8080], "ws-b": [3000]})

    m = list_all_web_routes()
    assert m == {"ws-a": [80, 8080], "ws-b": [3000]}


@patch("desk.web_routes._s3_client")
def test_get_ports_missing_workstation(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.side_effect = _no_such_key_error()

    assert get_ports("unknown") == []


@patch("desk.web_routes._s3_client")
def test_add_port_new(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.side_effect = _no_such_key_error()

    ports = add_port("my-ws", 443)

    assert ports == [443]
    s3.put_object.assert_called_once()
    saved = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert saved == {"my-ws": [443]}


@patch("desk.web_routes._s3_client")
def test_add_port_idempotent(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"my-ws": [443]})

    ports = add_port("my-ws", 443)

    assert ports == [443]
    s3.put_object.assert_not_called()


@patch("desk.web_routes._s3_client")
def test_add_port_sorts(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"my-ws": [9000]})

    ports = add_port("my-ws", 80)

    assert ports == [80, 9000]
    saved = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert saved["my-ws"] == [80, 9000]


@patch("desk.web_routes._s3_client")
def test_remove_port(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"my-ws": [80, 443]})

    ports = remove_port("my-ws", 80)

    assert ports == [443]
    saved = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert saved == {"my-ws": [443]}


@patch("desk.web_routes._s3_client")
def test_remove_port_drops_empty_workstation(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"my-ws": [80]})

    ports = remove_port("my-ws", 80)

    assert ports == []
    saved = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert saved == {}


@patch("desk.web_routes._s3_client")
def test_remove_port_not_found(mock_client: MagicMock) -> None:
    s3 = MagicMock()
    mock_client.return_value = s3
    s3.get_object.return_value = _make_s3_response_dict({"my-ws": [80]})

    with pytest.raises(ValueError, match="not registered"):
        remove_port("my-ws", 9999)


def test_add_port_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid port"):
        add_port("x", 0)  # type: ignore[arg-type] — exercise validation
