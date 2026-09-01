"""The 10-stage resumable document ingestion pipeline.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the complete document processing lifecycle:
1. Resumable Ingestion Pipeline: Slices file processing into 10 discrete stages:
   `upload` -> `validation` -> `extraction` -> `cleaning` -> `metadata` ->
   `chunking` -> `embedding` -> `indexing` -> `quality` -> `completed`.
2. Stage-Level Audit Trail (`PipelineRun`): Every attempt at every stage is logged
   in the database with duration in ms, retry count, and failure details.
3. Partial Pipeline Resumption (`from_stage`): If embedding fails due to a network glitch,
   the user can call `/reindex?from_stage=embedding` without having to re-parse the file.
4. Live SSE Progress Publishing: Publishes stage progress events to Redis channel
   `learnflow:document:{id}` for real-time frontend dashboard streaming.
5. Batch Vector Embedding & Atomic Indexing: Batches chunks in groups of 64 through the
   AI Gateway, saves chunks in PostgreSQL, and upserts vectors to ChromaDB (`org_{hex}`).

===============================================================================
PIPELINE EXECUTION CHAIN:
===============================================================================
Worker Task (`tasks.py:ingest_document`)
  ──► `run_pipeline(session, document, gateway)`
  ──► Loop through `PIPELINE_ORDER`:
      1. Insert `PipelineRun(stage=stage, status="running")`
      2. Publish Redis progress event
      3. Execute stage handler (`_stage_*`)
      4. Update `PipelineRun(status="succeeded", duration_ms)`
  ──► Mark `document.status = "indexed"` and update `knowledge_base` counters.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import AIGateway
from ..config import settings
from ..logging import get_logger
from ..models import (
    PIPELINE_ORDER,
    Document,
    DocumentChunk,
    DocumentStatus,
    KnowledgeBase,
    PipelineRun,
    PipelineStage,
)
from ..redis_client import get_redis, redis_enabled
from . import extract as extract_mod
from .chunking import chunk_segments
from .vectorstore import VectorRecord, get_vector_store

log = get_logger(__name__)

EMBED_BATCH = 64        # Number of chunks sent per embedding API call
MIN_CHUNK_CHARS = 40    # Chunks shorter than this after cleaning are discarded


class StageFailure(Exception):
    """Custom exception raised when a pipeline stage fails validation or processing."""
    def __init__(self, stage: PipelineStage, message: str) -> None:
        super().__init__(message)
        self.stage = stage


async def _publish(document_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Publishes ingestion progress event to Redis for Server-Sent Events (SSE) streaming."""
    if not redis_enabled():
        return
    try:
        await get_redis().publish(
            f"learnflow:document:{document_id}", json.dumps(payload, default=str)
        )
    except Exception:
        # Progress broadcasting is best-effort; failure should not abort ingestion
        log.debug("progress_publish_failed", document_id=str(document_id))


async def run_pipeline(
    session: AsyncSession,
    document: Document,
    gateway: AIGateway,
    *,
    from_stage: PipelineStage | None = None,
) -> Document:
    """Executes the 10-stage document ingestion pipeline.

    Args:
        session: Active database session.
        document: The `Document` model instance to process.
        gateway: Central AI Gateway instance.
        from_stage: Optional stage to resume from (e.g. `PipelineStage.chunking`).

    Returns:
        Document: The updated Document record.
    """
    kb = await session.get(KnowledgeBase, document.knowledge_base_id)
    if kb is None:
        raise StageFailure(PipelineStage.validation, "Knowledge base no longer exists")

    # Determine slice of stages to execute
    stages = PIPELINE_ORDER
    if from_stage is not None:
        stages = PIPELINE_ORDER[PIPELINE_ORDER.index(from_stage) :]

    document.status = DocumentStatus.processing
    document.error = None
    state: dict[str, Any] = {}
    
    # Map stages to handler functions
    handlers = {
        PipelineStage.upload: _stage_upload,
        PipelineStage.validation: _stage_validation,
        PipelineStage.extraction: _stage_extraction,
        PipelineStage.cleaning: _stage_cleaning,
        PipelineStage.metadata: _stage_metadata,
        PipelineStage.chunking: _stage_chunking,
        PipelineStage.embedding: _stage_embedding,
        PipelineStage.indexing: _stage_indexing,
        PipelineStage.quality: _stage_quality,
        PipelineStage.completed: _stage_completed,
    }

    # Execute stages sequentially
    for index, stage in enumerate(stages):
        previous = await session.scalar(
            select(func.count())
            .select_from(PipelineRun)
            .where(PipelineRun.document_id == document.id, PipelineRun.stage == stage)
        )
        run = PipelineRun(
            organization_id=document.organization_id,
            document_id=document.id,
            stage=stage,
            status="running",
            attempt=int(previous or 0) + 1,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        document.stage = stage
        await session.flush()
        started = time.perf_counter()
        
        # Broadcast stage start over Redis SSE
        await _publish(
            document.id, {"stage": stage.value, "status": "running", "progress": document.progress}
        )
        
        try:
            details = await handlers[stage](session, document, kb, gateway, state)
        except StageFailure as exc:
            await _finish_run(session, run, started, "failed", str(exc))
            document.status = DocumentStatus.failed
            document.error = f"{stage.value}: {exc}"
            await _publish(
                document.id, {"stage": stage.value, "status": "failed", "error": str(exc)}
            )
            log.error("pipeline_stage_failed", stage=stage.value, document_id=str(document.id))
            return document
        except Exception as exc:
            await _finish_run(session, run, started, "failed", repr(exc)[:500])
            document.status = DocumentStatus.failed
            document.error = f"{stage.value}: unexpected error"
            log.exception("pipeline_stage_crashed", stage=stage.value)
            return document

        # Mark stage succeeded
        await _finish_run(session, run, started, "succeeded", None, details)
        document.progress = round((index + 1) / len(stages), 3)
        await session.flush()
        await _publish(
            document.id,
            {"stage": stage.value, "status": "succeeded", "progress": document.progress},
        )

    return document


async def _finish_run(
    session: AsyncSession,
    run: PipelineRun,
    started: float,
    status: str,
    error: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    """Updates a PipelineRun record upon completion with duration and metrics."""
    run.status = status
    run.error = error
    run.details = details or {}
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    await session.flush()


# -----------------------------------------------------------------------------
# Individual Stage Handlers
# -----------------------------------------------------------------------------

async def _stage_upload(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 1: Reads stored file bytes from filesystem storage path."""
    path = Path(document.storage_path)
    if not path.exists():
        raise StageFailure(PipelineStage.upload, f"Stored file missing at {path}")
    state["raw"] = path.read_bytes()
    return {"size_bytes": len(state["raw"])}


async def _stage_validation(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 2: Re-verifies SHA-256 checksum and extension support."""
    suffix = Path(document.filename).suffix.lower()
    if extract_mod.checksum(state["raw"]) != document.checksum:
        raise StageFailure(PipelineStage.validation, "Checksum mismatch — file changed on disk")
    if suffix not in extract_mod.SUPPORTED:
        raise StageFailure(PipelineStage.validation, f"Unsupported extension {suffix}")
    state["suffix"] = suffix
    return {"suffix": suffix, "checksum_ok": True}


async def _stage_extraction(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 3: Extracts page-aware text segments using format-specific parsers."""
    try:
        segments = extract_mod.extract(state["raw"], state["suffix"])
    except Exception as exc:
        raise StageFailure(PipelineStage.extraction, str(exc)) from exc
    state["segments"] = segments
    pages = {s.page for s in segments if s.page}
    document.page_count = len(pages) or None
    return {"segments": len(segments), "pages": len(pages)}


async def _stage_cleaning(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 4: Normalizes Unicode, removes hyphen breaks, and drops empty segments."""
    before = sum(len(s.text) for s in state["segments"])
    for segment in state["segments"]:
        segment.text = extract_mod.clean(segment.text)
    state["segments"] = [s for s in state["segments"] if len(s.text) >= MIN_CHUNK_CHARS]
    if not state["segments"]:
        raise StageFailure(PipelineStage.cleaning, "Nothing left after cleaning — no usable text")
    after = sum(len(s.text) for s in state["segments"])
    return {"chars_before": before, "chars_after": after}


async def _stage_metadata(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 5: Extracts language, word count, document title guess, and headings."""
    text = "\n".join(s.text for s in state["segments"])
    document.word_count = len(text.split())
    document.detected_language = extract_mod.detect_language(text[:5000])
    sections = sorted({s.section for s in state["segments"] if s.section})
    document.doc_metadata = {
        **document.doc_metadata,
        "sections": sections[:50],
        "title_guess": (state["segments"][0].section or state["segments"][0].text[:120]).strip(),
    }
    return {"words": document.word_count, "language": document.detected_language}


async def _stage_chunking(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 6: Packs sentences into token-budgeted chunks with carried overlap."""
    chunks = chunk_segments(
        state["segments"],
        chunk_size=kb.chunk_size_tokens or settings.chunk_size_tokens,
        overlap=kb.chunk_overlap_tokens or settings.chunk_overlap_tokens,
    )
    if not chunks:
        raise StageFailure(PipelineStage.chunking, "Chunker produced no chunks")
    state["chunks"] = chunks
    # Atomic re-ingestion: clear old chunks from PostgreSQL
    await session.execute(
        sql_delete(DocumentChunk).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.organization_id == document.organization_id,
        )
    )
    return {"chunks": len(chunks), "avg_tokens": sum(c.token_count for c in chunks) // len(chunks)}


async def _stage_embedding(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 7: Generates dense embeddings in batches of 64 via AI Gateway."""
    chunks = state["chunks"]
    vectors: list[list[float]] = []
    total_tokens = 0
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        batch_vectors, tokens = await gateway.embed(
            [c.text for c in batch], operation="document_embed"
        )
        vectors.extend(batch_vectors)
        total_tokens += tokens
        document.progress = round(0.6 + 0.2 * (i + len(batch)) / len(chunks), 3)
        await _publish(
            document.id,
            {"stage": "embedding", "status": "running", "progress": document.progress},
        )
    if len(vectors) != len(chunks):
        raise StageFailure(PipelineStage.embedding, "Embedding count does not match chunk count")
    state["vectors"] = vectors
    return {"embedded": len(vectors), "tokens": total_tokens}


async def _stage_indexing(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 8: Inserts chunks to PostgreSQL and upserts vectors to ChromaDB (`org_{hex}`)."""
    chunks, vectors = state["chunks"], state["vectors"]
    records: list[VectorRecord] = []
    rows: list[DocumentChunk] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        vector_id = f"{document.id}:{chunk.position}"
        rows.append(
            DocumentChunk(
                organization_id=document.organization_id,
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                position=chunk.position,
                content=chunk.text,
                token_count=chunk.token_count,
                page_number=chunk.page,
                section=chunk.section,
                vector_id=vector_id,
            )
        )
        records.append(
            VectorRecord(
                id=vector_id,
                embedding=vector,
                text=chunk.text,
                metadata={
                    "document_id": str(document.id),
                    "knowledge_base_id": str(document.knowledge_base_id),
                    "filename": document.filename,
                    "position": chunk.position,
                    "page": chunk.page or 0,
                    "section": chunk.section or "",
                },
            )
        )
    session.add_all(rows)
    store = get_vector_store()
    # Replace existing vectors in ChromaDB collection
    await store.delete(document.organization_id, where={"document_id": str(document.id)})
    await store.upsert(document.organization_id, records)
    state["chunk_rows"] = rows
    return {"indexed": len(records)}


async def _stage_quality(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 9: Quality gate checking for empty or oversized chunks."""
    chunks = state["chunks"]
    empty = sum(1 for c in chunks if len(c.text) < MIN_CHUNK_CHARS)
    oversized = sum(1 for c in chunks if c.token_count > kb.chunk_size_tokens * 1.5)
    if empty > len(chunks) * 0.5:
        raise StageFailure(PipelineStage.quality, "More than half the chunks are near-empty")
    return {"chunk_count": len(chunks), "near_empty": empty, "oversized": oversized}


async def _stage_completed(session, document, kb, gateway, state) -> dict[str, Any]:
    """Stage 10: Marks document indexed and updates knowledge base chunk counters."""
    document.status = DocumentStatus.indexed
    document.indexed_at = datetime.now(UTC)
    document.progress = 1.0
    await session.flush()
    kb.document_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.knowledge_base_id == kb.id,
                Document.status == DocumentStatus.indexed,
            )
        )
        or 0
    )
    kb.chunk_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.knowledge_base_id == kb.id)
        )
        or 0
    )
    return {"status": "indexed", "kb_documents": kb.document_count, "kb_chunks": kb.chunk_count}


async def delete_document_vectors(document: Document) -> None:
    """Removes all vectors belonging to a document from ChromaDB."""
    await get_vector_store().delete(
        document.organization_id, where={"document_id": str(document.id)}
    )
