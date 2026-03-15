"""
Lambda authorizer for API Gateway HTTP API.
Validates Cognito JWT (signature, issuer, expiry) and ensures email claim matches ALLOWED_EMAIL.
"""
import json
import os
from typing import Any

try:
    import jwt
    from jwt import PyJWKClient
except ImportError:
    jwt = None
    PyJWKClient = None


def handler(event: dict, context: Any) -> dict:
    allowed_email = os.environ.get("ALLOWED_EMAIL", "").strip().lower()
    if not allowed_email:
        return _deny()

    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return _deny()
    token = auth[7:].strip()
    if not token:
        return _deny()

    user_pool_id = os.environ.get("USER_POOL_ID", "").strip()
    region = os.environ.get("AWS_REGION", "").strip()
    client_id = os.environ.get("CLIENT_ID", "").strip()
    if not user_pool_id or not region:
        return _deny()

    if not jwt or not PyJWKClient:
        return _deny()

    try:
        jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_exp": True},
            audience=client_id if client_id else None,
            issuer=f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        )
        email = (payload.get("email") or "").strip().lower()
        if email != allowed_email:
            return _deny()
        return _allow()
    except Exception:
        return _deny()


def _allow() -> dict:
    return {"isAuthorized": True}


def _deny() -> dict:
    return {"isAuthorized": False}
