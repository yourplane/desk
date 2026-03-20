"""Saved command storage backed by S3."""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import ClientError

from desk.config import get_default_profile, get_default_region
from desk.log import get_logger

log = get_logger("saved_commands")

S3_KEY = "saved-commands.json"


@dataclass
class SavedCommandParam:
    name: str
    default: str | None = None


@dataclass
class SavedCommand:
    id: str
    name: str
    script: str
    description: str = ""
    parameters: list[SavedCommandParam] = field(default_factory=list)


def _get_data_bucket() -> str:
    bucket = os.environ.get("DESK_DATA_BUCKET")
    if not bucket:
        raise RuntimeError("DESK_DATA_BUCKET environment variable is not set")
    return bucket


def _s3_client():
    region = get_default_region()
    profile = get_default_profile()
    session = boto3.Session(region_name=region, profile_name=profile)
    return session.client("s3")


def _load_all() -> list[dict[str, Any]]:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    try:
        resp = s3.get_object(Bucket=bucket, Key=S3_KEY)
        data = json.loads(resp["Body"].read())
        return data if isinstance(data, list) else []
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return []
        raise


def _save_all(commands: list[dict[str, Any]]) -> None:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    s3.put_object(
        Bucket=bucket,
        Key=S3_KEY,
        Body=json.dumps(commands, indent=2),
        ContentType="application/json",
    )


def _to_dataclass(d: dict[str, Any]) -> SavedCommand:
    params = [
        SavedCommandParam(name=p["name"], default=p.get("default"))
        for p in d.get("parameters", [])
    ]
    return SavedCommand(
        id=d["id"],
        name=d["name"],
        script=d["script"],
        description=d.get("description", ""),
        parameters=params,
    )


def _to_dict(cmd: SavedCommand) -> dict[str, Any]:
    return {
        "id": cmd.id,
        "name": cmd.name,
        "script": cmd.script,
        "description": cmd.description,
        "parameters": [
            {"name": p.name, **({"default": p.default} if p.default is not None else {})}
            for p in cmd.parameters
        ],
    }


def list_saved_commands() -> list[SavedCommand]:
    """Return all saved commands."""
    return [_to_dataclass(d) for d in _load_all()]


def get_saved_command(cmd_id: str) -> SavedCommand:
    """Return a single saved command by ID. Raises ValueError if not found."""
    for d in _load_all():
        if d["id"] == cmd_id:
            return _to_dataclass(d)
    raise ValueError(f"Saved command '{cmd_id}' not found")


def create_saved_command(
    name: str,
    script: str,
    description: str = "",
    parameters: list[dict[str, Any]] | None = None,
) -> SavedCommand:
    """Create and persist a new saved command. Returns the created command."""
    commands = _load_all()
    cmd_id = secrets.token_hex(4)
    entry: dict[str, Any] = {
        "id": cmd_id,
        "name": name,
        "script": script,
        "description": description,
        "parameters": parameters or [],
    }
    commands.append(entry)
    _save_all(commands)
    log.info("created saved command id=%s name=%s", cmd_id, name)
    return _to_dataclass(entry)


def update_saved_command(cmd_id: str, **fields: Any) -> SavedCommand:
    """Update fields of a saved command. Raises ValueError if not found."""
    commands = _load_all()
    for i, d in enumerate(commands):
        if d["id"] == cmd_id:
            for k, v in fields.items():
                if v is not None:
                    d[k] = v
            commands[i] = d
            _save_all(commands)
            log.info("updated saved command id=%s fields=%s", cmd_id, list(fields.keys()))
            return _to_dataclass(d)
    raise ValueError(f"Saved command '{cmd_id}' not found")


def delete_saved_command(cmd_id: str) -> None:
    """Delete a saved command by ID. Raises ValueError if not found."""
    commands = _load_all()
    new_commands = [d for d in commands if d["id"] != cmd_id]
    if len(new_commands) == len(commands):
        raise ValueError(f"Saved command '{cmd_id}' not found")
    _save_all(new_commands)
    log.info("deleted saved command id=%s", cmd_id)


def render_script(command: SavedCommand, params: dict[str, str]) -> str:
    """Substitute ``{{key}}`` placeholders with parameter values.

    Falls back to parameter defaults for keys not in *params*.
    Raises ValueError for required parameters (no default) that are missing.
    """
    result = command.script
    for p in command.parameters:
        placeholder = "{{" + p.name + "}}"
        if placeholder not in result:
            continue
        value = params.get(p.name)
        if value is None:
            value = p.default
        if value is None:
            raise ValueError(
                f"Missing required parameter '{p.name}' for command '{command.name}'"
            )
        result = result.replace(placeholder, value)
    return result


def extract_parameters(script: str) -> list[str]:
    """Extract unique ``{{param}}`` placeholder names from a script template."""
    seen: set[str] = set()
    result: list[str] = []
    for m in re.finditer(r"\{\{(\w+)\}\}", script):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result
