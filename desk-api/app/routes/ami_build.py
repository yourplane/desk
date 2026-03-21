"""AMI recipe CRUD and Step Functions–backed cloud AMI builds."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.ami_recipe import validate_recipe_body

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ami-build"])

RECIPES_TABLE = os.environ.get("DESK_AMI_RECIPES_TABLE", "")
BUILDS_TABLE = os.environ.get("DESK_AMI_BUILDS_TABLE", "")
SFN_ARN = os.environ.get("DESK_AMI_BUILD_STATE_MACHINE_ARN", "")


def _ddb():
    return boto3.client("dynamodb")


def _sfn():
    return boto3.client("stepfunctions")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_ami_config() -> None:
    if not RECIPES_TABLE or not BUILDS_TABLE or not SFN_ARN:
        raise HTTPException(
            status_code=503,
            detail="AMI build is not configured (missing table or state machine env).",
        )


class CreateRecipeBody(BaseModel):
    name: str
    body: dict[str, Any]


class UpdateRecipeBody(BaseModel):
    name: str | None = None
    body: dict[str, Any] | None = None


class StartBuildBody(BaseModel):
    recipe_id: str


def _item_to_recipe(item: dict[str, Any]) -> dict[str, Any]:
    body_raw = item.get("body", {}).get("S", "{}")
    try:
        body = json.loads(body_raw)
    except json.JSONDecodeError:
        body = {}
    return {
        "recipe_id": item["recipe_id"]["S"],
        "name": item.get("name", {}).get("S", ""),
        "body": body if isinstance(body, dict) else {},
        "updated_at": item.get("updated_at", {}).get("S", ""),
        "created_at": item.get("created_at", {}).get("S", ""),
    }


def _item_to_build(item: dict[str, Any]) -> dict[str, Any]:
    def _s(key: str) -> str | None:
        v = item.get(key, {})
        if "S" in v:
            return v["S"]
        return None

    out = {
        "build_id": item["build_id"]["S"],
        "recipe_id": _s("recipe_id") or "",
        "recipe_name": _s("recipe_name") or "",
        "status": _s("status") or "unknown",
        "workstation_name": _s("workstation_name"),
        "instance_id": _s("instance_id"),
        "ami_id": _s("ami_id"),
        "ami_name": _s("ami_name"),
        "execution_arn": _s("execution_arn"),
        "error_message": _s("error_message"),
        "updated_at": _s("updated_at") or "",
        "created_at": _s("created_at") or "",
    }
    return out


@router.get("/ami-recipes")
def list_recipes():
    """List saved AMI recipes."""
    _require_ami_config()
    try:
        r = _ddb().scan(TableName=RECIPES_TABLE)
    except Exception as e:
        logger.exception("list_recipes: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    items = r.get("Items", [])
    recipes = [_item_to_recipe(i) for i in items]
    recipes.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return recipes


@router.post("/ami-recipes")
def create_recipe(body: CreateRecipeBody):
    """Create a recipe."""
    _require_ami_config()
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name must not be empty.")
    try:
        validate_recipe_body(body.body, cloud=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    rid = str(uuid.uuid4())
    ts = _now_iso()
    try:
        _ddb().put_item(
            TableName=RECIPES_TABLE,
            Item={
                "recipe_id": {"S": rid},
                "name": {"S": name},
                "body": {"S": json.dumps(body.body)},
                "created_at": {"S": ts},
                "updated_at": {"S": ts},
            },
        )
    except Exception as e:
        logger.exception("create_recipe: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"recipe_id": rid, "name": name, "body": body.body, "updated_at": ts}


@router.get("/ami-recipes/{recipe_id}")
def get_recipe(recipe_id: str):
    _require_ami_config()
    r = _ddb().get_item(
        TableName=RECIPES_TABLE,
        Key={"recipe_id": {"S": recipe_id}},
        ConsistentRead=True,
    )
    if "Item" not in r:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    return _item_to_recipe(r["Item"])


@router.put("/ami-recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: UpdateRecipeBody):
    _require_ami_config()
    existing = get_recipe(recipe_id)
    new_name = body.name.strip() if body.name is not None else existing["name"]
    new_body = body.body if body.body is not None else existing["body"]
    try:
        validate_recipe_body(new_body, cloud=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ts = _now_iso()
    _ddb().put_item(
        TableName=RECIPES_TABLE,
        Item={
            "recipe_id": {"S": recipe_id},
            "name": {"S": new_name},
            "body": {"S": json.dumps(new_body)},
            "created_at": {"S": existing.get("created_at") or ts},
            "updated_at": {"S": ts},
        },
    )
    return {**existing, "name": new_name, "body": new_body, "updated_at": ts}


@router.delete("/ami-recipes/{recipe_id}")
def delete_recipe(recipe_id: str):
    _require_ami_config()
    _ddb().delete_item(
        TableName=RECIPES_TABLE,
        Key={"recipe_id": {"S": recipe_id}},
    )
    return {"deleted": True, "recipe_id": recipe_id}


@router.post("/ami-builds")
def start_build(body: StartBuildBody):
    """Start a Step Functions execution for an AMI build."""
    _require_ami_config()
    recipe_id = body.recipe_id.strip()
    if not recipe_id:
        raise HTTPException(status_code=400, detail="recipe_id required.")
    r = _ddb().get_item(
        TableName=RECIPES_TABLE,
        Key={"recipe_id": {"S": recipe_id}},
        ConsistentRead=True,
    )
    if "Item" not in r:
        raise HTTPException(status_code=404, detail="Recipe not found.")
    recipe = _item_to_recipe(r["Item"])
    try:
        validate_recipe_body(recipe["body"], cloud=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    build_id = str(uuid.uuid4())
    ts = _now_iso()
    _ddb().put_item(
        TableName=BUILDS_TABLE,
        Item={
            "build_id": {"S": build_id},
            "recipe_id": {"S": recipe_id},
            "recipe_name": {"S": recipe.get("name") or ""},
            "status": {"S": "queued"},
            "created_at": {"S": ts},
            "updated_at": {"S": ts},
        },
    )
    try:
        resp = _sfn().start_execution(
            stateMachineArn=SFN_ARN,
            name=build_id,
            input=json.dumps({"build_id": build_id, "recipe_id": recipe_id}),
        )
        arn = resp["executionArn"]
        _ddb().update_item(
            TableName=BUILDS_TABLE,
            Key={"build_id": {"S": build_id}},
            UpdateExpression="SET execution_arn = :e, #s = :st, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":e": {"S": arn},
                ":st": {"S": "running"},
                ":u": {"S": _now_iso()},
            },
        )
    except Exception as e:
        logger.exception("start_execution: %s", e)
        _ddb().update_item(
            TableName=BUILDS_TABLE,
            Key={"build_id": {"S": build_id}},
            UpdateExpression="SET #s = :st, error_message = :err, updated_at = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":st": {"S": "failed"},
                ":err": {"S": str(e)[:8000]},
                ":u": {"S": _now_iso()},
            },
        )
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"build_id": build_id, "execution_arn": arn, "status": "running"}


@router.get("/ami-builds")
def list_builds():
    _require_ami_config()
    try:
        r = _ddb().scan(TableName=BUILDS_TABLE)
    except Exception as e:
        logger.exception("list_builds: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    items = [_item_to_build(i) for i in r.get("Items", [])]
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items


@router.get("/ami-builds/{build_id}")
def get_build(build_id: str):
    _require_ami_config()
    r = _ddb().get_item(
        TableName=BUILDS_TABLE,
        Key={"build_id": {"S": build_id}},
        ConsistentRead=True,
    )
    if "Item" not in r:
        raise HTTPException(status_code=404, detail="Build not found.")
    out = _item_to_build(r["Item"])
    arn = out.get("execution_arn")
    if arn:
        try:
            ex = _sfn().describe_execution(executionArn=arn)
            out["execution_status"] = ex.get("status")
            out["execution_start_date"] = ex.get("startDate").isoformat() if ex.get("startDate") else None
            out["execution_stop_date"] = ex.get("stopDate").isoformat() if ex.get("stopDate") else None
        except Exception as e:
            logger.debug("describe_execution: %s", e)
            out["execution_status"] = None
    return out
