"""Saved command CRUD routes."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.saved_commands import (
    create_saved_command,
    delete_saved_command,
    get_saved_command,
    list_saved_commands,
    update_saved_command,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["saved-commands"])


class _ParamModel(BaseModel):
    name: str
    default: str | None = None


class CreateSavedCommandBody(BaseModel):
    name: str
    script: str
    description: str = ""
    parameters: list[_ParamModel] = []


class UpdateSavedCommandBody(BaseModel):
    name: str | None = None
    script: str | None = None
    description: str | None = None
    parameters: list[_ParamModel] | None = None


def _param_dicts(params: list[_ParamModel]) -> list[dict[str, Any]]:
    return [
        {"name": p.name, **({"default": p.default} if p.default is not None else {})}
        for p in params
    ]


def _serialize(cmd) -> dict[str, Any]:
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


@router.get("/saved-commands")
def list_saved_commands_route():
    """Return all saved commands."""
    try:
        commands = list_saved_commands()
    except Exception as e:
        logger.exception("list_saved_commands failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return [_serialize(c) for c in commands]


@router.post("/saved-commands")
def create_saved_command_route(body: CreateSavedCommandBody):
    """Create a new saved command."""
    name = body.name.strip()
    script = body.script.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name must not be empty.")
    if not script:
        raise HTTPException(status_code=400, detail="Script must not be empty.")
    try:
        cmd = create_saved_command(
            name=name,
            script=body.script,
            description=body.description,
            parameters=_param_dicts(body.parameters),
        )
    except Exception as e:
        logger.exception("create_saved_command failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _serialize(cmd)


@router.put("/saved-commands/{cmd_id}")
def update_saved_command_route(cmd_id: str, body: UpdateSavedCommandBody):
    """Update an existing saved command."""
    fields: dict[str, Any] = {}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Name must not be empty.")
        fields["name"] = body.name.strip()
    if body.script is not None:
        if not body.script.strip():
            raise HTTPException(status_code=400, detail="Script must not be empty.")
        fields["script"] = body.script
    if body.description is not None:
        fields["description"] = body.description
    if body.parameters is not None:
        fields["parameters"] = _param_dicts(body.parameters)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update.")
    try:
        cmd = update_saved_command(cmd_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("update_saved_command failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _serialize(cmd)


@router.delete("/saved-commands/{cmd_id}")
def delete_saved_command_route(cmd_id: str):
    """Delete a saved command."""
    try:
        delete_saved_command(cmd_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception("delete_saved_command failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"deleted": True}
