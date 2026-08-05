"""Knowledge bases and document ingestion."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from ..config import settings
from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..errors import Conflict, ValidationFailed
from ..models import Document, DocumentChunk, DocumentStatus, KnowledgeBase, PipelineStage
from ..rag import extract as extract_mod
from ..rag.pipeline import delete_document_vectors
from ..redis_client import get_redis
from ..schemas.common import Page, PageParams, page_params
from ..schemas.knowledge import (
    ChunkOut,
    DocumentDetail,
    DocumentOut,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    PipelineRunOut,
)
from ..services import audit, jobs

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])
documents_router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=Page[KnowledgeBaseOut], summary="List knowledge bases")
async def list_kbs(
    repo: RepoDep, params: PageParams = Depends(page_params)
) -> Page[KnowledgeBaseOut]:
    items, total = await repo.list(
        KnowledgeBase, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[KnowledgeBaseOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "",
    response_model=KnowledgeBaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("document:write"))],
    summary="Create a knowledge base",
)
async def create_kb(
    body: KnowledgeBaseCreate,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> KnowledgeBase:
    kb = repo.add(KnowledgeBase(**body.model_dump(), embedding_model=settings.embedding_model))
    await repo.flush()
    await audit.record(
        session, principal, "knowledge_base.create", "knowledge_base", str(kb.id), request=request
    )
    return kb


@router.get("/{kb_id}", response_model=KnowledgeBaseOut, summary="Knowledge base detail")
async def get_kb(kb_id: uuid.UUID, repo: RepoDep) -> KnowledgeBase:
    return await repo.get(KnowledgeBase, kb_id)


@router.delete(
    "/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("document:write"))],
    summary="Delete a knowledge base and every vector it owns",
)
async def delete_kb(
    kb_id: uuid.UUID, repo: RepoDep, session: SessionDep, principal: PrincipalDep, request: Request
) -> None:
    kb = await repo.get(KnowledgeBase, kb_id)
    docs, _ = await repo.list(Document, where=[Document.knowledge_base_id == kb.id], limit=100)
    for document in docs:
        await delete_document_vectors(document)
    await repo.delete(kb)
    await audit.record(
        session, principal, "knowledge_base.delete", "knowledge_base", str(kb_id), request=request
    )


# ------------------------------------------------------------------ documents
@documents_router.get("", response_model=Page[DocumentOut], summary="List documents")
async def list_documents(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    knowledge_base_id: uuid.UUID | None = Query(None),
    doc_status: DocumentStatus | None = Query(None, alias="status"),
) -> Page[DocumentOut]:
    where = []
    if knowledge_base_id:
        where.append(Document.knowledge_base_id == knowledge_base_id)
    if doc_status:
        where.append(Document.status == doc_status)
    items, total = await repo.list(
        Document, where=where, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[DocumentOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@documents_router.post(
    "",
    response_model=DocumentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("document:write"))],
    summary="Upload a document and queue ingestion",
)
async def upload_document(
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
    knowledge_base_id: uuid.UUID = Query(...),
    file: UploadFile = File(...),
) -> DocumentDetail:
    kb = await repo.get(KnowledgeBase, knowledge_base_id)
    data = await file.read()
    suffix = extract_mod.validate_upload(
        file.filename or "upload", file.content_type or "", data, settings.max_upload_bytes
    )
    digest = extract_mod.checksum(data)

    duplicate = await session.scalar(
        repo.select(Document).where(
            Document.checksum == digest,
            Document.knowledge_base_id == kb.id,
            Document.status != DocumentStatus.deleted,
        )
    )
    if duplicate is not None:
        raise Conflict(
            "This exact file is already in the knowledge base",
            {"document_id": str(duplicate.id)},
        )

    document = repo.add(
        Document(
            knowledge_base_id=kb.id,
            filename=file.filename or f"upload{suffix}",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(data),
            checksum=digest,
            storage_path="",
            uploaded_by=principal.user.id if principal.user else None,
        )
    )
    await repo.flush()

    target = Path(settings.upload_dir) / str(repo.organization_id)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{document.id}{suffix}"
    path.write_bytes(data)
    document.storage_path = str(path)
    await repo.flush()

    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "ingest_document",
        idempotency_key=f"ingest:{document.id}",
        resource_type="document",
        resource_id=str(document.id),
        document_id=str(document.id),
    )
    await audit.record(
        session,
        principal,
        "document.upload",
        "document",
        str(document.id),
        request=request,
        details={"filename": document.filename, "size_bytes": len(data)},
    )
    # Build from the flat serializer: a freshly-flushed instance would lazy-load `runs`,
    # which is illegal on an async session.
    detail = DocumentDetail(**DocumentOut.model_validate(document).model_dump(), job_id=job.id)
    return detail


@documents_router.get("/{document_id}", response_model=DocumentDetail, summary="Document detail")
async def get_document(
    document_id: uuid.UUID, repo: RepoDep, session: SessionDep
) -> DocumentDetail:
    document = await repo.get(Document, document_id)
    detail = DocumentDetail(**DocumentOut.model_validate(document).model_dump())
    detail.chunk_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
        )
        or 0
    )
    detail.runs = [PipelineRunOut.model_validate(r) for r in document.runs]
    return detail


@documents_router.get(
    "/{document_id}/chunks", response_model=Page[ChunkOut], summary="Inspect stored chunks"
)
async def list_chunks(
    document_id: uuid.UUID, repo: RepoDep, params: PageParams = Depends(page_params)
) -> Page[ChunkOut]:
    await repo.get(Document, document_id)
    items, total = await repo.list(
        DocumentChunk,
        where=[DocumentChunk.document_id == document_id],
        sort="position",
        limit=params.limit,
        offset=params.offset,
    )
    return Page(
        items=[ChunkOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@documents_router.post(
    "/{document_id}/reindex",
    response_model=DocumentDetail,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require("document:write"))],
    summary="Re-run ingestion, optionally from a specific stage",
)
async def reindex(
    document_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
    from_stage: PipelineStage | None = Query(None),
) -> DocumentDetail:
    document = await repo.get(Document, document_id)
    if not Path(document.storage_path).exists():
        raise ValidationFailed("Original file is no longer on disk; re-upload it")
    document.status = DocumentStatus.pending
    document.progress = 0.0
    document.error = None
    await repo.flush()
    job = await jobs.enqueue(
        session,
        repo.organization_id,
        "ingest_document",
        idempotency_key=f"ingest:{document.id}:{uuid.uuid4().hex[:8]}",
        resource_type="document",
        resource_id=str(document.id),
        document_id=str(document.id),
        from_stage=from_stage.value if from_stage else None,
    )
    await audit.record(
        session, principal, "document.reindex", "document", str(document.id), request=request
    )
    return DocumentDetail(**DocumentOut.model_validate(document).model_dump(), job_id=job.id)


@documents_router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("document:write"))],
    summary="Delete a document, its chunks and its vectors",
)
async def delete_document(
    document_id: uuid.UUID,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> None:
    document = await repo.get(Document, document_id)
    await delete_document_vectors(document)
    path = Path(document.storage_path)
    if path.exists():
        path.unlink()
    kb = await session.get(KnowledgeBase, document.knowledge_base_id)
    await repo.delete(document)
    await session.flush()
    if kb is not None:
        kb.document_count = max(0, kb.document_count - 1)
    await audit.record(
        session, principal, "document.delete", "document", str(document_id), request=request
    )


@documents_router.get(
    "/{document_id}/stream",
    summary="Server-sent events: live ingestion progress",
    response_class=EventSourceResponse,
)
async def stream_progress(document_id: uuid.UUID, repo: RepoDep):
    document = await repo.get(Document, document_id)

    async def publisher():
        # Emit current state immediately so a late subscriber is not left staring at nothing.
        yield {
            "event": "progress",
            "data": json.dumps(
                {
                    "stage": document.stage.value,
                    "status": document.status.value,
                    "progress": document.progress,
                }
            ),
        }
        if document.status in (DocumentStatus.indexed, DocumentStatus.failed):
            yield {"event": "done", "data": json.dumps({"status": document.status.value})}
            return

        pubsub = get_redis().pubsub()
        await pubsub.subscribe(f"learnflow:document:{document_id}")
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                payload = message["data"]
                yield {"event": "progress", "data": payload}
                try:
                    parsed = json.loads(payload)
                except (TypeError, ValueError):
                    continue
                if (
                    parsed.get("stage") == PipelineStage.completed.value
                    or parsed.get("status") == "failed"
                ):
                    yield {"event": "done", "data": payload}
                    return
        except asyncio.CancelledError:  # client disconnected
            raise
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return EventSourceResponse(publisher())
