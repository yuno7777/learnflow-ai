"""API keys, audit log, background job status and integration health."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, status

from ..automation import n8n_client
from ..config import settings
from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..errors import Conflict
from ..models import APIKey, AuditLog, Job
from ..rag.vectorstore import get_vector_store
from ..redis_client import get_redis
from ..schemas.automation import APIKeyCreate, APIKeyCreated, APIKeyOut, AuditLogOut, JobOut
from ..schemas.common import Page, PageParams, page_params
from ..security import generate_api_key
from ..services import audit

keys_router = APIRouter(prefix="/api-keys", tags=["platform"])
audit_router = APIRouter(prefix="/audit-logs", tags=["platform"])
jobs_router = APIRouter(prefix="/jobs", tags=["platform"])
integrations_router = APIRouter(prefix="/integrations", tags=["platform"])


@keys_router.get(
    "",
    response_model=Page[APIKeyOut],
    dependencies=[Depends(require("apikey:manage"))],
    summary="List API keys (hashes are never returned)",
)
async def list_keys(repo: RepoDep, params: PageParams = Depends(page_params)) -> Page[APIKeyOut]:
    items, total = await repo.list(
        APIKey, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[APIKeyOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@keys_router.post(
    "",
    response_model=APIKeyCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("apikey:manage"))],
    summary="Create an API key — the raw value is shown once",
)
async def create_key(
    body: APIKeyCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> APIKeyCreated:
    raw, prefix, key_hash = generate_api_key()
    record = repo.add(
        APIKey(
            name=body.name,
            prefix=prefix,
            key_hash=key_hash,
            scopes=body.scopes,
            created_by=principal.user.id if principal.user else None,
            expires_at=(
                datetime.now(UTC) + timedelta(days=body.expires_in_days)
                if body.expires_in_days
                else None
            ),
        )
    )
    await repo.flush()
    await audit.record(
        session,
        principal,
        "api_key.create",
        "api_key",
        str(record.id),
        request=request,
        details={"name": body.name, "prefix": prefix},
    )
    return APIKeyCreated(**APIKeyOut.model_validate(record).model_dump(), key=raw)


@keys_router.delete(
    "/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("apikey:manage"))],
    summary="Revoke an API key",
)
async def revoke_key(
    key_id: uuid.UUID, repo: RepoDep, session: SessionDep, principal: PrincipalDep, request: Request
) -> None:
    record = await repo.get(APIKey, key_id)
    if record.revoked_at is not None:
        raise Conflict("Key is already revoked")
    record.revoked_at = datetime.now(UTC)
    await repo.flush()
    await audit.record(
        session, principal, "api_key.revoke", "api_key", str(key_id), request=request
    )


@audit_router.get(
    "",
    response_model=Page[AuditLogOut],
    dependencies=[Depends(require("audit:read"))],
    summary="Audit trail",
)
async def list_audit(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
) -> Page[AuditLogOut]:
    where = []
    if action:
        where.append(AuditLog.action == action)
    if resource_type:
        where.append(AuditLog.resource_type == resource_type)
    items, total = await repo.list(
        AuditLog, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[AuditLogOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@jobs_router.get("", response_model=Page[JobOut], summary="Background jobs")
async def list_jobs(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    job_status: str | None = Query(None, alias="status"),
    kind: str | None = Query(None),
) -> Page[JobOut]:
    where = []
    if job_status:
        where.append(Job.status == job_status)
    if kind:
        where.append(Job.kind == kind)
    items, total = await repo.list(
        Job, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[JobOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@jobs_router.get("/{job_id}", response_model=JobOut, summary="Job status and progress")
async def get_job(job_id: uuid.UUID, repo: RepoDep) -> Job:
    return await repo.get(Job, job_id)


@integrations_router.get("", summary="Connected service health and configuration")
async def integrations(repo: RepoDep) -> dict[str, dict]:
    try:
        redis_ok = bool(await get_redis().ping())
    except Exception:
        redis_ok = False
    try:
        vector_count = await get_vector_store().count(repo.organization_id)
        vector_ok = True
    except Exception:
        vector_count, vector_ok = 0, False

    return {
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "fallback": settings.llm_fallback_provider,
            "configured": settings.llm_provider == "mock"
            or bool(settings.openai_api_key)
            or settings.llm_provider == "ollama",
        },
        "embeddings": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
        },
        "vector_store": {"engine": "chromadb", "healthy": vector_ok, "vectors": vector_count},
        "redis": {"healthy": redis_ok},
        "n8n": {"base_url": settings.n8n_base_url, "healthy": await n8n_client.health()},
        "email": {"adapter": "smtp" if settings.smtp_host else "mock"},
    }
