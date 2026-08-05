"""Password hashing, JWTs, API keys and HMAC webhook signatures.

No secrets are logged or returned; API keys and refresh tokens are stored hashed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from .config import settings
from .errors import Unauthorized

# --------------------------------------------------------------------------- passwords


def _prehash(password: str) -> bytes:
    """bcrypt silently truncates at 72 bytes; SHA-256 first so long passwords stay distinct."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode())
    except ValueError:
        return False


# --------------------------------------------------------------------------- JWT

TokenType = Literal["access", "refresh"]


def create_access_token(
    user_id: uuid.UUID, organization_id: uuid.UUID | None, role: str | None
) -> tuple[str, int]:
    expires_in = settings.access_token_ttl_minutes * 60
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(organization_id) if organization_id else None,
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def decode_token(token: str, expected_type: TokenType = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("Invalid token") from exc
    if payload.get("type") != expected_type:
        raise Unauthorized("Wrong token type")
    return payload


def create_refresh_token() -> tuple[str, str, datetime]:
    """Returns (raw_token, sha256_hash, expiry). Only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    expires = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    return raw, sha256_hex(raw), expires


# --------------------------------------------------------------------------- API keys

API_KEY_PREFIX = "lf_"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, display_prefix, hash). Raw key is shown to the user exactly once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12], sha256_hex(raw)


# --------------------------------------------------------------------------- webhooks


def sign_payload(body: bytes, timestamp: int | None = None, secret: str | None = None) -> str:
    """`t=<unix>,v1=<hex hmac>` over `<t>.<body>` — Stripe-style, so n8n can verify it easily."""
    ts = timestamp if timestamp is not None else int(time.time())
    key = (secret or settings.webhook_signing_secret).encode()
    mac = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(body: bytes, header: str, secret: str | None = None) -> str:
    """Constant-time verify + freshness window. Returns the v1 mac (used as a replay key)."""
    try:
        parts = dict(part.split("=", 1) for part in header.split(","))
        ts = int(parts["t"])
        provided = parts["v1"]
    except (ValueError, KeyError) as exc:
        raise Unauthorized("Malformed signature header") from exc

    if abs(time.time() - ts) > settings.webhook_tolerance_seconds:
        raise Unauthorized("Signature timestamp outside tolerance window")

    key = (secret or settings.webhook_signing_secret).encode()
    expected = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise Unauthorized("Signature mismatch")
    return provided
