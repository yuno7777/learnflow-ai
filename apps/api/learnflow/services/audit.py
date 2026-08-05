"""Audit log writer. Never receives secret material — callers pass ids and labels only."""

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
