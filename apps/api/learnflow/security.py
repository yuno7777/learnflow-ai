"""Cryptographic primitives: password hashing, JWTs, API keys, and HMAC signatures.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file is the cryptographic security foundation of LearnFlow AI. It handles:
1. Passwords: Pre-hashing with SHA-256 before bcrypt (work factor 12) to eliminate
   bcrypt's silent 72-byte truncation vulnerability.
2. JWT Access Tokens: Encoding and decoding stateless HS256 tokens carrying
   `sub` (user_id), `org` (organization_id), and `role` claims.
3. Refresh Tokens: Generating cryptographically secure random tokens, returning
   the raw token for the client, and storing ONLY the SHA-256 hash in PostgreSQL.
4. API Keys (`lf_...`): Generating random machine keys, returning the raw key
   once at creation, and saving the SHA-256 hash for database lookups.
5. HMAC Webhook Signatures: Generating and verifying Stripe-style timestamped
   signatures (`t=<unix>,v1=<hmac>`) with constant-time equality checks and
   tolerance window enforcement to prevent timing and replay attacks.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `routers/auth.py`: Calls `hash_password`, `verify_password`, `create_access_token`,
  and `create_refresh_token` for user onboarding, login, and token rotation.
- `deps.py`: Calls `decode_token` to authenticate JWTs, and `sha256_hex` to verify API keys.
- `routers/platform.py`: Calls `generate_api_key` when an admin creates a new API key.
- `automation/n8n_client.py`: Calls `sign_payload` to sign outbound webhooks dispatched to n8n.
- `routers/webhooks.py`: Calls `verify_signature` on all inbound callbacks from n8n.
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

# =============================================================================
# 1. Passwords (Pre-hashed SHA-256 + Bcrypt)
# =============================================================================

def _prehash(password: str) -> bytes:
    """Pre-hashes password with SHA-256 before sending to bcrypt.

    SECURITY CONCEPT (The Bcrypt 72-byte truncation flaw):
    Standard bcrypt ignores any characters after the 72nd byte of a password.
    If a user enters an 80-character passphrase, the last 8 characters are ignored.
    
    By computing `Base64(SHA256(password))` first, any input length is reduced to a
    uniform 44-character ASCII string, preserving all input entropy while retaining
    bcrypt's memory-hard salt and key-stretching protection.
    """
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    """Hashes a plaintext password using bcrypt with 12 salt rounds."""
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Verifies a plaintext password against a stored bcrypt hash in constant time."""
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode())
    except ValueError:
        return False


# =============================================================================
# 2. JSON Web Tokens (JWT)
# =============================================================================

TokenType = Literal["access", "refresh"]


def create_access_token(
    user_id: uuid.UUID, organization_id: uuid.UUID | None, role: str | None
) -> tuple[str, int]:
    """Generates a short-lived HS256 JWT access token.

    Data Payload Claims:
    - `sub`: User ID string.
    - `org`: Active Organization ID string (optional).
    - `role`: Role string within the organization.
    - `type`: Must be "access".
    - `iat` / `exp`: Issued-at and expiration Unix timestamps.
    - `jti`: Unique token nonce (prevents collision).

    Returns:
        tuple[str, int]: (encoded_jwt_string, expires_in_seconds)
    """
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
    """Validates signature and expiration, returning decoded JWT claims.

    Throws:
        Unauthorized: If token signature is invalid, expired, or wrong type.
    """
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
    """Generates a single-use rotating refresh token.

    Security Design:
    - Raw token is a 48-byte cryptographically secure random string.
    - Raw token is returned to the client once.
    - PostgreSQL stores ONLY `sha256_hex(raw_token)` in `refresh_tokens`.
      If the database is leaked, an attacker cannot use the hashed tokens to generate sessions.

    Returns:
        tuple[str, str, datetime]: (raw_token, sha256_hash, expiration_datetime)
    """
    raw = secrets.token_urlsafe(48)
    expires = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    return raw, sha256_hex(raw), expires


# =============================================================================
# 3. Machine API Keys
# =============================================================================

API_KEY_PREFIX = "lf_"


def sha256_hex(value: str) -> str:
    """Computes standard hexadecimal SHA-256 digest of a string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Creates a new API key for automated integrations (e.g. n8n, CI/CD).

    Format: `lf_<32_byte_urlsafe_token>`
    
    Returns:
        tuple[str, str, str]:
        - raw_key: Shown to the admin once upon creation.
        - prefix: Safe display prefix (e.g. `lf_Ab3d9...`) for identification in dashboard.
        - hash: SHA-256 hash saved in the `api_keys.key_hash` database column.
    """
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:12], sha256_hex(raw)


# =============================================================================
# 4. Webhook Signatures & Replay Prevention
# =============================================================================

def sign_payload(body: bytes, timestamp: int | None = None, secret: str | None = None) -> str:
    """Generates a Stripe-style HMAC-SHA256 signature header for outbound webhooks.

    Signature Format: `t=<unix_timestamp>,v1=<hex_hmac_digest>`
    Calculated over: `b"<unix_timestamp>." + raw_body_bytes`

    Used by: `learnflow/automation/n8n_client.py` when dispatching events to n8n.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    key = (secret or settings.webhook_signing_secret).encode()
    mac = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_signature(body: bytes, header: str, secret: str | None = None) -> str:
    """Validates an incoming webhook signature against timestamp and HMAC secret.

    Security checks:
    1. Parses `t` and `v1` components from the header.
    2. Enforces Freshness Window: Rejects requests if `|now - t| > 300 seconds`
       (prevents an attacker from capturing and replaying old requests).
    3. Constant-Time Comparison: Uses `hmac.compare_digest` to prevent timing attacks.

    Returns:
        str: The valid `v1` HMAC digest string. Downstream routes (`routers/webhooks.py`)
             insert this digest into `webhook_receipts.signature` (unique constraint)
             to guarantee that a valid webhook payload cannot be replayed twice within the 300s window.
    """
    try:
        parts = dict(part.split("=", 1) for part in header.split(","))
        ts = int(parts["t"])
        provided = parts["v1"]
    except (ValueError, KeyError) as exc:
        raise Unauthorized("Malformed signature header") from exc

    # Check 5-minute tolerance window
    if abs(time.time() - ts) > settings.webhook_tolerance_seconds:
        raise Unauthorized("Signature timestamp outside tolerance window")

    key = (secret or settings.webhook_signing_secret).encode()
    expected = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    
    # Constant-time comparison
    if not hmac.compare_digest(expected, provided):
        raise Unauthorized("Signature mismatch")
    return provided
