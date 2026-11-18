from datetime import datetime, timedelta, timezone
import secrets
from typing import Any

import jwt
from pymongo import ReturnDocument

from app.security import token_digest


def issue_access_token(
    user_id: str,
    secret_key: str,
    ttl_minutes: int,
    token_version: int = 0,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(timezone.utc)
    return jwt.encode(
        {
            "user_id": user_id,
            "type": "access",
            "token_version": token_version,
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=ttl_minutes),
        },
        secret_key,
        algorithm="HS256",
    )


def issue_refresh_token(
    user_id: str,
    secret_key: str,
    ttl_days: int,
    sessions: Any,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(timezone.utc)
    token_id = secrets.token_urlsafe(32)
    expires_at = issued_at + timedelta(days=ttl_days)
    token = jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "jti": token_id,
            "iat": issued_at,
            "exp": expires_at,
        },
        secret_key,
        algorithm="HS256",
    )
    sessions.insert_one(
        {
            "token_digest": token_digest(token_id),
            "user_id": user_id,
            "created_at": issued_at,
            "expires_at": expires_at,
            "revoked_at": None,
        }
    )
    return token


def decode_access_token(token: str, secret_key: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        secret_key,
        algorithms=["HS256"],
        options={"require": ["exp", "user_id"]},
    )
    if payload.get("type") not in (None, "access"):
        raise jwt.InvalidTokenError("Invalid token type")
    return payload


def decode_refresh_token(token: str, secret_key: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        secret_key,
        algorithms=["HS256"],
        options={"require": ["exp", "sub", "jti", "type"]},
    )
    if payload.get("type") != "refresh":
        raise jwt.InvalidTokenError("Invalid token type")
    return payload


def revoke_refresh_token(
    token: str,
    secret_key: str,
    sessions: Any,
    now: datetime | None = None,
) -> bool:
    try:
        payload = decode_refresh_token(token, secret_key)
    except jwt.InvalidTokenError:
        return False

    revoked_at = now or datetime.now(timezone.utc)
    result = sessions.update_one(
        {
            "token_digest": token_digest(payload["jti"]),
            "revoked_at": None,
            "expires_at": {"$gt": revoked_at},
        },
        {"$set": {"revoked_at": revoked_at}},
    )
    return result.modified_count == 1


def consume_refresh_token(
    token: str,
    secret_key: str,
    sessions: Any,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    try:
        payload = decode_refresh_token(token, secret_key)
    except jwt.InvalidTokenError:
        return None

    consumed_at = now or datetime.now(timezone.utc)
    session = sessions.find_one_and_update(
        {
            "token_digest": token_digest(payload["jti"]),
            "user_id": payload["sub"],
            "revoked_at": None,
            "expires_at": {"$gt": consumed_at},
        },
        {"$set": {"revoked_at": consumed_at}},
        return_document=ReturnDocument.BEFORE,
    )
    if not session:
        return None
    return payload
