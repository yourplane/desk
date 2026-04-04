"""Web route port registry stored in S3 (logical intent; not wired to actual routing)."""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from desk.config import get_desk_settings
from desk.log import get_logger

log = get_logger("web_routes")

S3_KEY = "web-routes.json"


def _get_data_bucket() -> str:
    bucket = os.environ.get("DESK_DATA_BUCKET")
    if not bucket:
        raise RuntimeError("DESK_DATA_BUCKET environment variable is not set")
    return bucket


def _s3_client():
    aws = get_desk_settings().aws_settings
    session = boto3.Session(region_name=aws.region, profile_name=aws.profile)
    return session.client("s3")


def _validate_port(port: int) -> int:
    p = int(port)
    if p < 1 or p > 65535:
        raise ValueError(f"Invalid port: {port!r} (expected 1–65535)")
    return p


def _normalize_workstation_name(name: str) -> str:
    n = str(name).strip()
    if not n:
        raise ValueError("Workstation name must not be empty")
    return n


def _coerce_ports(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(_validate_port(int(x)))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _load_map() -> dict[str, list[int]]:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    try:
        resp = s3.get_object(Bucket=bucket, Key=S3_KEY)
        data = json.loads(resp["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return {}
        raise
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[int]] = {}
    for k, v in data.items():
        if not isinstance(k, str):
            continue
        key = k.strip()
        if not key:
            continue
        result[key] = _coerce_ports(v)
    return result


def _save_map(m: dict[str, list[int]]) -> None:
    s3 = _s3_client()
    bucket = _get_data_bucket()
    # Omit empty port lists to keep the object small
    slim = {k: ports for k, ports in sorted(m.items()) if ports}
    s3.put_object(
        Bucket=bucket,
        Key=S3_KEY,
        Body=json.dumps(slim, indent=2),
        ContentType="application/json",
    )


def list_all_web_routes() -> dict[str, list[int]]:
    """Return a copy of all workstation → port lists."""
    return dict(_load_map())


def get_ports(workstation_name: str) -> list[int]:
    """Return sorted unique ports for *workstation_name* (may be empty)."""
    name = _normalize_workstation_name(workstation_name)
    m = _load_map()
    return list(m.get(name, []))


def add_port(workstation_name: str, port: int) -> list[int]:
    """Register *port* for *workstation_name*. Idempotent if already present."""
    name = _normalize_workstation_name(workstation_name)
    p = _validate_port(port)
    m = _load_map()
    current = list(m.get(name, []))
    if p in current:
        log.info("web route already registered name=%s port=%s", name, p)
        return sorted(current)
    current.append(p)
    m[name] = sorted(set(current))
    _save_map(m)
    log.info("registered web route name=%s port=%s", name, p)
    return list(m[name])


def remove_port(workstation_name: str, port: int) -> list[int]:
    """Remove *port* for *workstation_name*. Raises ValueError if the port is not registered."""
    name = _normalize_workstation_name(workstation_name)
    p = _validate_port(port)
    m = _load_map()
    current = list(m.get(name, []))
    if p not in current:
        raise ValueError(f"Port {p} is not registered for workstation {name!r}")
    m[name] = sorted([x for x in current if x != p])
    if not m[name]:
        del m[name]
    _save_map(m)
    log.info("removed web route name=%s port=%s", name, p)
    return list(m.get(name, []))
