"""Audit logging service for compliance and security forensics.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file records security-sensitive actions across the platform:
1. `record()`: Appends an immutable `AuditLog` row to the database.
2. Captures Actor Identity: Records whether the action was taken by a human `User`,
   a machine `APIKey`, a `Learner`, or a background `System` task.
3. Captures Request Context: Extracts client IP address and correlation `request_id`.
4. Secret Scrubbing: Callers pass entity IDs and labels only—raw API keys or passwords
   are never accepted.

===============================================================================
WHERE THIS IS CALLED FROM:
===============================================================================
- `routers/auth.py`: On login, logout, API key creation, and API key revocation.
- `routers/courses.py`, `routers/knowledge.py`: On document and course deletions.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import Principal
from ..models import AuditLog


async def record(
    session: AsyncSession,
    principal: Principal | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    organization_id: Any = None,
    request: Request | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Records an audit log entry for a platform action.

    Args:
        session: Active database session.
        principal: Authenticated caller principal (or None for system events).
        action: Action name (e.g. 'auth.login', 'apikey.create', 'course.delete').
        resource_type: Target entity type (e.g. 'course', 'apikey', 'document').
        resource_id: Optional UUID string of the affected entity.
        organization_id: Explicit tenant override.
        request: FastAPI HTTP request to extract IP address and request ID.
        details: Optional non-sensitive JSON metadata dictionary.

    Returns:
        AuditLog: The added AuditLog entity.
    """
    entry = AuditLog(
        organization_id=organization_id or (principal.organization_id if principal else None),
        actor_type=principal.actor_type if principal else "system",
        actor_id=principal.actor_id if principal else None,
        actor_label=principal.actor_label if principal else "system",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        request_id=getattr(request.state, "request_id", None) if request else None,
        ip_address=(request.client.host if request and request.client else None),
        details=details or {},
    )
    session.add(entry)
    return entry
