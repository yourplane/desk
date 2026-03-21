"""AMI recipe CRUD and Step Functions–backed cloud AMI builds (metadata in S3)."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from desk.ami_recipe import validate_recipe_body

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ami-build"])

DATA_BUCKET = os.environ.get("DESK_DATA_BUCKET", "")
# Object keys: {prefix}/{id}.json under DESK_DATA_BUCKET
RECIPES_PREFIX = os.environ.get("DESK_AMI_RECIPES_PREFIX", "ami-recipes").strip().strip("/")
BUILDS_PREFIX = os.environ.get("DESK_AMI_BUILDS_PREFIX", "ami-builds").strip().strip("/")
SFN_ARN = os.environ.get("DESK_AMI_BUILD_STATE_MACHINE_ARN", "")


def _s3():
    return boto3.client("s3")


def _sfn():
    return boto3.client("stepfunctions")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recipe_key(recipe_id: str) -> str:
    return f"{RECIPES_PREFIX}/{recipe_id}.json"


def _build_key(build_id: str) -> str:
    return f"{BUILDS_PREFIX}/{build_id}.json"


def _require_ami_config() -> None:
    if not DATA_BUCKET or not SFN_ARN:
        raise HTTPException(
            status_code=503,
            detail="AMI build is not configured (missing data bucket or state machine env).",
        )


class CreateRecipeBody(BaseModel):
    name: str
    body: dict[str, Any]


class UpdateRecipeBody(BaseModel):
    name: str | None = None
    body: dict[str, Any] | None = None


class StartBuildBody(BaseModel):
    recipe_id: str


def _load_recipe_obj(data: dict[str, Any]) -> dict[str, Any]:
    body = data.get("body")
    if not isinstance(body, dict):
        body = {}
    return {
        "recipe_id": data.get("recipe_id", ""),
        "name": data.get("name", "") or "",
        "body": body,
        "updated_at": data.get("updated_at", "") or "",
        "created_at": data.get("created_at", "") or "",
    }


def _load_build_obj(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "build_id": data.get("build_id", ""),
        "recipe_id": data.get("recipe_id", "") or "",
        "recipe_name": data.get("recipe_name", "") or "",
        "status": data.get("status", "unknown") or "unknown",
        "workstation_name": data.get("workstation_name"),
        "instance_id": data.get("instance_id"),
        "ami_id": data.get("ami_id"),
        "ami_name": data.get("ami_name"),
        "execution_arn": data.get("execution_arn"),
        "error_message": data.get("error_message"),
        "updated_at": data.get("updated_at", "") or "",
        "created_at": data.get("created_at", "") or "",
    }


def _get_json_or_404(
    bucket: str, key: str, *, not_found_detail: str = "Not found."
) -> dict[str, Any]:
    try:
        r = _s3().get_object(Bucket=bucket, Key=key)
        raw = r["Body"].read().decode("utf-8")
        return json.loads(raw)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise HTTPException(status_code=404, detail=not_found_detail) from e
        raise


@router.get("/ami-recipes")
def list_recipes():
    """List saved AMI recipes."""
    _require_ami_config()
    prefix = f"{RECIPES_PREFIX}/"
    try:
        paginator = _s3().get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=DATA_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj.get("Key", "")
                if k.endswith(".json"):
                    keys.append(k)
        recipes: list[dict[str, Any]] = []
        for key in keys:
            try:
                r = _s3().get_object(Bucket=DATA_BUCKET, Key=key)
                data = json.loads(r["Body"].read().decode("utf-8"))
                recipes.append(_load_recipe_obj(data))
            except Exception as e:
                logger.warning("skip recipe key=%s: %s", key, e)
        recipes.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return recipes
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_recipes: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


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
    doc = {
        "recipe_id": rid,
        "name": name,
        "body": body.body,
        "created_at": ts,
        "updated_at": ts,
    }
    try:
        _s3().put_object(
            Bucket=DATA_BUCKET,
            Key=_recipe_key(rid),
            Body=json.dumps(doc).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as e:
        logger.exception("create_recipe: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"recipe_id": rid, "name": name, "body": body.body, "updated_at": ts}


@router.get("/ami-recipes/{recipe_id}")
def get_recipe(recipe_id: str):
    _require_ami_config()
    data = _get_json_or_404(
        DATA_BUCKET, _recipe_key(recipe_id), not_found_detail="Recipe not found."
    )
    return _load_recipe_obj(data)


@router.put("/ami-recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: UpdateRecipeBody):
    _require_ami_config()
    existing_raw = _get_json_or_404(
        DATA_BUCKET, _recipe_key(recipe_id), not_found_detail="Recipe not found."
    )
    existing = _load_recipe_obj(existing_raw)
    new_name = body.name.strip() if body.name is not None else existing["name"]
    new_body = body.body if body.body is not None else existing["body"]
    try:
        validate_recipe_body(new_body, cloud=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    ts = _now_iso()
    doc = {
        "recipe_id": recipe_id,
        "name": new_name,
        "body": new_body,
        "created_at": existing.get("created_at") or ts,
        "updated_at": ts,
    }
    _s3().put_object(
        Bucket=DATA_BUCKET,
        Key=_recipe_key(recipe_id),
        Body=json.dumps(doc).encode("utf-8"),
        ContentType="application/json",
    )
    return {**existing, "name": new_name, "body": new_body, "updated_at": ts}


@router.delete("/ami-recipes/{recipe_id}")
def delete_recipe(recipe_id: str):
    _require_ami_config()
    try:
        _s3().delete_object(Bucket=DATA_BUCKET, Key=_recipe_key(recipe_id))
    except Exception as e:
        logger.exception("delete_recipe: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"deleted": True, "recipe_id": recipe_id}


@router.post("/ami-builds")
def start_build(body: StartBuildBody):
    """Start a Step Functions execution for an AMI build."""
    _require_ami_config()
    recipe_id = body.recipe_id.strip()
    if not recipe_id:
        raise HTTPException(status_code=400, detail="recipe_id required.")
    try:
        r = _s3().get_object(Bucket=DATA_BUCKET, Key=_recipe_key(recipe_id))
        recipe_data = json.loads(r["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise HTTPException(status_code=404, detail="Recipe not found.") from e
        raise HTTPException(status_code=500, detail=str(e)) from e
    recipe = _load_recipe_obj(recipe_data)
    try:
        validate_recipe_body(recipe["body"], cloud=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    build_id = str(uuid.uuid4())
    ts = _now_iso()
    build_doc = {
        "build_id": build_id,
        "recipe_id": recipe_id,
        "recipe_name": recipe.get("name") or "",
        "status": "queued",
        "created_at": ts,
        "updated_at": ts,
    }
    try:
        _s3().put_object(
            Bucket=DATA_BUCKET,
            Key=_build_key(build_id),
            Body=json.dumps(build_doc).encode("utf-8"),
            ContentType="application/json",
        )
        resp = _sfn().start_execution(
            stateMachineArn=SFN_ARN,
            name=build_id,
            input=json.dumps({"build_id": build_id, "recipe_id": recipe_id}),
        )
        arn = resp["executionArn"]
        build_doc["execution_arn"] = arn
        build_doc["status"] = "running"
        build_doc["updated_at"] = _now_iso()
        _s3().put_object(
            Bucket=DATA_BUCKET,
            Key=_build_key(build_id),
            Body=json.dumps(build_doc).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as e:
        logger.exception("start_execution: %s", e)
        build_doc["status"] = "failed"
        build_doc["error_message"] = str(e)[:8000]
        build_doc["updated_at"] = _now_iso()
        try:
            _s3().put_object(
                Bucket=DATA_BUCKET,
                Key=_build_key(build_id),
                Body=json.dumps(build_doc).encode("utf-8"),
                ContentType="application/json",
            )
        except Exception:
            logger.exception("failed to persist build failure")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"build_id": build_id, "execution_arn": arn, "status": "running"}


@router.get("/ami-builds")
def list_builds():
    _require_ami_config()
    prefix = f"{BUILDS_PREFIX}/"
    try:
        paginator = _s3().get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=DATA_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                k = obj.get("Key", "")
                if k.endswith(".json"):
                    keys.append(k)
        items: list[dict[str, Any]] = []
        for key in keys:
            try:
                r = _s3().get_object(Bucket=DATA_BUCKET, Key=key)
                data = json.loads(r["Body"].read().decode("utf-8"))
                items.append(_load_build_obj(data))
            except Exception as e:
                logger.warning("skip build key=%s: %s", key, e)
        items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return items
    except Exception as e:
        logger.exception("list_builds: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ami-builds/{build_id}")
def get_build(build_id: str):
    _require_ami_config()
    data = _get_json_or_404(
        DATA_BUCKET, _build_key(build_id), not_found_detail="Build not found."
    )
    out = _load_build_obj(data)
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
