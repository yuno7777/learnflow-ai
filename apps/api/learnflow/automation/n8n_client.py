"""Outbound n8n integration client: Signed webhooks and REST execution reconciliation.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages outbound communication to the external n8n workflow engine:
1. `trigger_webhook`: Serializes payload to exact JSON bytes, signs with HMAC-SHA256
   via `security.sign_payload()`, and sends HTTP POST with `X-LearnFlow-Signature`.
2. `get_execution`: Queries n8n REST API to fetch execution status and step outputs.
3. `health`: Pings n8n's `/healthz` endpoint to check container readiness.

===============================================================================
WHY BYTE-EXACT SERIALIZATION MATTERS (HMAC Integrity):
===============================================================================
HMAC signatures verify the *exact byte sequence* of the HTTP body. If Python serializes
JSON with spaces (`{"a": 1}`) but n8n evaluates without spaces (`{"a":1}`), the HMAC
digest will mismatch. This client enforces compact serialization (`separators=(",", ":")`)
before computing the signature.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import settings
from ..logging import get_logger
from ..security import sign_payload

log = get_logger(__name__)


class N8NUnavailable(Exception):
    """Raised when the n8n container is offline, unreachable, or returns a 5xx error."""
    pass


async def trigger_webhook(
    path: str, payload: dict[str, Any], *, timeout: float = 20.0
) -> dict[str, Any]:
    """Sends a signed JSON payload to an n8n webhook endpoint.

    Headers attached:
    - `X-LearnFlow-Signature`: `t=<unix>,v1=<hmac>` computed over raw body.
    - `X-LearnFlow-Event-Id`: Execution ID string.
    """
    # Byte-exact compact JSON serialization
    body = json.dumps(payload, default=str, separators=(",", ":")).encode()
    signature = sign_payload(body)
    url = f"{settings.n8n_base_url.rstrip('/')}/webhook/{path.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "X-LearnFlow-Signature": signature,
        "X-LearnFlow-Event-Id": str(payload.get("execution_id", "")),
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise N8NUnavailable(f"n8n unreachable: {exc}") from exc

    if response.status_code >= 400:
        raise N8NUnavailable(f"n8n returned HTTP {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:1000]}


async def get_execution(n8n_execution_id: str) -> dict[str, Any] | None:
    """Fetches execution details from the n8n REST API (used by worker reconciliation cron)."""
    if not settings.n8n_api_key:
        return None
    url = f"{settings.n8n_base_url.rstrip('/')}/api/v1/executions/{n8n_execution_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers={"X-N8N-API-KEY": settings.n8n_api_key})
    except httpx.HTTPError as exc:
        log.warning("n8n_execution_fetch_failed", error=str(exc))
        return None
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        log.warning("n8n_execution_fetch_status", status=response.status_code)
        return None
    return response.json()


async def health() -> bool:
    """Checks if the n8n cluster is online and responding."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.n8n_base_url.rstrip('/')}/healthz")
            return response.status_code < 400
    except httpx.HTTPError:
        return False
