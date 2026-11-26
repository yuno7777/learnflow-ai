"""Knowledge base management and document ingestion API router.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages RAG knowledge bases and uploaded files:
1. Knowledge Base CRUD (`/knowledge-bases`): Create, list, inspect, and delete collections.
   Deleting a knowledge base automatically purges its vectors from ChromaDB.
2. Document Upload (`POST /documents`):
   - Validates file extensions and binary magic bytes.
   - Calculates SHA-256 checksum to reject duplicate uploads.
   - Saves file to tenant storage path (`/uploads/{org_id}/{doc_id}.pdf`).
   - Enqueues background ingestion job (`ingest_document`).
3. Document Inspection (`GET /documents/{id}` & `/chunks`): Returns stage progress and chunk tokens.
4. Resumable Reindexing (`POST /documents/{id}/reindex?from_stage=...`): Triggers re-ingestion.
5. Live SSE Progress (`GET /documents/{id}/stream`): Streams real-time pipeline progress
   events (`upload` -> `validation` -> ... -> `indexing`) using Server-Sent Events over Redis.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `rag/extract.py`: Validates uploads and computes file checksums.
- `rag/pipeline.py`: Executes the 10-stage ingestion pipeline.
- `worker/tasks.py`: Ingestion is scheduled as an async ARQ background job.
"""

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
from ..redis_client import get_redis, redis_enabled
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


# -----------------------------------------------------------------------------
# Knowledge Base Endpoints
# -----------------------------------------------------------------------------


@router.get("", response_model=Page[KnowledgeBaseOut], summary="List knowledge bases")
async def list_kbs(
    repo: RepoDep, params: PageParams = Depends(page_params)
) -> Page[KnowledgeBaseOut]:
    """Lists all knowledge base containers in the tenant."""
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
    """Creates a new knowledge base collection for RAG grounding."""
    kb = repo.add(KnowledgeBase(**body.model_dump(), embedding_model=settings.embedding_model))
    await repo.flush()
    await audit.record(
        session, principal, "knowledge_base.create", "knowledge_base", str(kb.id), request=request
    )
    return kb


@router.get("/{kb_id}", response_model=KnowledgeBaseOut, summary="Knowledge base detail")
async def get_kb(kb_id: uuid.UUID, repo: RepoDep) -> KnowledgeBase:
    """Fetches metadata and document/chunk counts for a knowledge base."""
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
    """Deletes a knowledge base, its documents, and purges all vectors from ChromaDB."""
    kb = await repo.get(KnowledgeBase, kb_id)
    docs, _ = await repo.list(Document, where=[Document.knowledge_base_id == kb.id], limit=100)
    for document in docs:
        await delete_document_vectors(document)
    await repo.delete(kb)
    await audit.record(
        session, principal, "knowledge_base.delete", "knowledge_base", str(kb_id), request=request
    )


# -----------------------------------------------------------------------------
# Document Endpoints
# -----------------------------------------------------------------------------


@documents_router.get("", response_model=Page[DocumentOut], summary="List documents")
async def list_documents(
    repo: RepoDep,
    params: PageParams = Depends(page_params),
    knowledge_base_id: uuid.UUID | None = Query(None),
    doc_status: DocumentStatus | None = Query(None, alias="status"),
) -> Page[DocumentOut]:
    """Lists documents with optional filters by knowledge base and ingestion status."""
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
    """Uploads a PDF, DOCX, CSV, or Markdown file and dispatches async background ingestion."""
    kb = await repo.get(KnowledgeBase, knowledge_base_id)
    data = await file.read()

    # 1. Validate magic bytes, extensions, and size limits
    suffix = extract_mod.validate_upload(
        file.filename or "upload", file.content_type or "", data, settings.max_upload_bytes
    )
    digest = extract_mod.checksum(data)

    # 2. Check for duplicate file checksums in the knowledge base
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

    # 3. Create document record and save file bytes to disk
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

    # 4. Enqueue background ingestion task
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
    detail = DocumentDetail(**DocumentOut.model_validate(document).model_dump(), job_id=job.id)
    return detail


@documents_router.get("/{document_id}", response_model=DocumentDetail, summary="Document detail")
async def get_document(
    document_id: uuid.UUID, repo: RepoDep, session: SessionDep
) -> DocumentDetail:
    """Returns detailed document metadata and pipeline attempt audit records."""
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
    """Lists the indexed text chunks, token counts, and page numbers for a document."""
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
    """Re-runs the ingestion pipeline for a document, optionally resuming from a specific stage."""
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
    """Deletes a document, removes vectors from ChromaDB, and deletes the file from disk."""
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
    """Streams live Server-Sent Events (SSE) of ingestion progress subscribed via Redis."""
    document = await repo.get(Document, document_id)

    async def publisher():
        # Emit initial state
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
        if (
            document.status in (DocumentStatus.indexed, DocumentStatus.failed)
            or not redis_enabled()
        ):
            yield {"event": "done", "data": json.dumps({"status": document.status.value})}
            return

        # Subscribe to Redis live channel
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
        except asyncio.CancelledError:
            raise
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return EventSourceResponse(publisher())
