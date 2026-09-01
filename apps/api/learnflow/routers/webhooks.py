"""Inbound signed webhook router with replay defense and idempotency receipts.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file securely receives inbound HTTP webhooks from external systems:
1. Cryptographic HMAC Verification (`_verify`): Validates `X-LearnFlow-Signature: t=<unix>,v1=<hmac>`
   over the raw request body bytes.
2. Replay Protection (`_record_receipt`): Inserts the signature into `webhook_receipts`.
   PostgreSQL's unique database constraint on `signature` guarantees that identical or
   replayed webhooks fail immediately with 401 Unauthorized.
3. `POST /webhooks/n8n/callback`: Receives workflow step outputs and completion statuses
   from external n8n workflow executions (`apply_callback`).
4. `POST /webhooks/events/{org_slug}/{event_name}`: Allows external HRIS or LMS systems
   to trigger domain events into LearnFlow.

===============================================================================
SECURITY FLOW (Webhook Ingestion):
===============================================================================
Inbound POST (Raw Bytes, Signature Header)
  ──► Constant-Time HMAC-SHA256 Verification (`security.verify_signature`)
  ──► Freshness Window Check (Rejected if timestamp older than 300s)
  ──► Database Unique Constraint Insert (`WebhookReceipt(signature=mac)`)
  ──► If Duplicate Signature: `IntegrityError` ──► `401 Unauthorized (Replayed)`
  ──► Process Callback / Emit Event
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..automation import events
from ..automation.engine import apply_callback
from ..db import get_session
from ..errors import NotFound, Unauthorized
from ..logging import get_logger
from ..models import Organization, WebhookReceipt, WorkflowExecution
from ..schemas.automation import ExecutionCallback
from ..security import verify_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _verify(request_headers, body: bytes) -> str:
    """Verifies HMAC signature over raw body bytes and returns the MAC hex string."""
    signature_header = request_headers.get("X-LearnFlow-Signature")
    if not signature_header:
        raise Unauthorized("Missing X-LearnFlow-Signature header")
    return verify_signature(body, signature_header)


async def _record_receipt(
    session: AsyncSession,
    *,
    source: str,
    mac: str,
    organization_id: uuid.UUID,
    event_id: str | None,
    payload: dict[str, Any],
) -> WebhookReceipt:
    """Inserts a webhook receipt. The database unique constraint on `signature` blocks replay attacks."""
    receipt = WebhookReceipt(
        organization_id=organization_id,
        source=source,
        signature=mac,
        event_id=event_id,
        received_at=datetime.now(UTC),
        payload=payload,
    )
    session.add(receipt)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Unauthorized("Replayed webhook signature") from exc
    return receipt


@router.post(
    "/n8n/callback",
    status_code=status.HTTP_202_ACCEPTED,
    summary="n8n reports back on an execution it was handed",
)
async def n8n_callback(request: Request, session: SessionDep) -> dict[str, Any]:
    """Inbound webhook called by n8n upon workflow completion."""
    body = await request.body()
    mac = _verify(request.headers, body)
    callback = ExecutionCallback.model_validate_json(body)

    # 1. Look up target execution
    execution = await session.get(WorkflowExecution, callback.execution_id)
    if execution is None:
        raise NotFound("Unknown execution id")
        
    # 2. Record unique receipt for replay protection
    await _record_receipt(
        session,
        source="n8n",
        mac=mac,
        organization_id=execution.organization_id,
        event_id=request.headers.get("X-LearnFlow-Event-Id"),
        payload={"execution_id": str(callback.execution_id), "status": callback.status.value},
    )

    # 3. Apply steps and final status
    await apply_callback(
        session,
        execution,
        status=callback.status,
        output=callback.output,
        error=callback.error,
        steps=callback.steps,
        n8n_execution_id=callback.n8n_execution_id,
        input_tokens=callback.input_tokens,
        output_tokens=callback.output_tokens,
    )
    log.info(
        "n8n_callback",
        execution_id=str(execution.id),
        status=callback.status.value,
        steps=len(callback.steps),
    )
    return {"accepted": True, "execution_id": str(execution.id)}


@router.post(
    "/events/{organization_slug}/{event_name}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="External systems emit a domain event into LearnFlow",
)
async def inbound_event(
    organization_slug: str,
    event_name: str,
    request: Request,
    session: SessionDep,
    x_learnflow_event_id: str | None = Header(default=None),
) -> dict[str, Any]:
    """Inbound webhook allowing third-party applications to trigger domain events."""
    organization = await session.scalar(
        select(Organization).where(Organization.slug == organization_slug)
    )
    if organization is None or not organization.is_active:
        raise NotFound("Unknown organization")

    body = await request.body()
    mac = _verify(request.headers, body)
    await _record_receipt(
        session,
        source="external",
        mac=mac,
        organization_id=organization.id,
        event_id=x_learnflow_event_id,
        payload={"event": event_name},
    )
    payload = json.loads(body or b"{}")
    execution_ids = await events.emit(
        session,
        organization.id,
        event_name,
        payload,
        idempotency_key=x_learnflow_event_id,
    )
    return {"accepted": True, "executions": [str(i) for i in execution_ids]}
