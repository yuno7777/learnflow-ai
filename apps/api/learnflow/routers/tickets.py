"""Support tickets. Creating one emits `ticket.created`, which drives the triage workflow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status

from ..automation import events
from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..models import SupportTicket
from ..schemas.automation import TicketCreate, TicketOut, TicketUpdate
from ..schemas.common import Page, PageParams, page_params
from ..services import audit

router = APIRouter(prefix="/tickets", tags=["support"])


@router.get("", response_model=Page[TicketOut], summary="List support tickets")
async def list_tickets(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    ticket_status: str | None = Query(None, alias="status"),
    category: str | None = Query(None),
    escalated: bool | None = Query(None),
) -> Page[TicketOut]:
    where = []
    if ticket_status:
        where.append(SupportTicket.status == ticket_status)
    if category:
        where.append(SupportTicket.category == category)
    if escalated is not None:
        where.append(SupportTicket.escalated.is_(escalated))
    items, total = await repo.list(
        SupportTicket, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[TicketOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a ticket (AI triage runs in the background)",
)
async def create_ticket(
    body: TicketCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> SupportTicket:
    ticket = repo.add(
        SupportTicket(subject=body.subject, body=body.body, learner_id=body.learner_id)
    )
    await repo.flush()
    await audit.record(
        session, principal, "ticket.create", "support_ticket", str(ticket.id), request=request
    )
    if body.triage:
        await events.emit(
            session,
            repo.organization_id,
            "ticket.created",
            {"ticket_id": str(ticket.id)},
            learner_id=body.learner_id,
            idempotency_key=str(ticket.id),
        )
    return ticket


@router.get("/{ticket_id}", response_model=TicketOut, summary="Ticket detail with AI decision")
async def get_ticket(ticket_id: uuid.UUID, repo: RepoDep) -> SupportTicket:
    return await repo.get(SupportTicket, ticket_id)


@router.patch(
    "/{ticket_id}",
    response_model=TicketOut,
    dependencies=[Depends(require("ticket:manage"))],
    summary="Update ticket status, category or assignment",
)
async def update_ticket(ticket_id: uuid.UUID, body: TicketUpdate, repo: RepoDep) -> SupportTicket:
    ticket = await repo.get(SupportTicket, ticket_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(ticket, field, value)
    if body.status in ("resolved", "closed") and ticket.resolved_at is None:
        ticket.resolved_at = datetime.now(UTC)
    await repo.flush()
    return ticket
