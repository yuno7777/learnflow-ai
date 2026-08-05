"""Outbound n8n integration: signed webhook triggers + REST execution lookups."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import settings
from ..logging import get_logger
from ..security import sign_payload

log = get_logger(__name__)


class N8NUnavailable(Exception):
    pass


async def trigger_webhook(
    path: str, payload: dict[str, Any], *, timeout: float = 20.0
) -> dict[str, Any]:
    """POST a signed payload to an n8n webhook.

    The signature covers the exact bytes we send, so the n8n side must verify against the raw
    body — not a re-serialised copy.
    """
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
    """Read an execution back from the n8n REST API (needs N8N_API_KEY)."""
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
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.n8n_base_url.rstrip('/')}/healthz")
            return response.status_code < 400
    except httpx.HTTPError:
        return False
