"""Knowledge bases, uploaded documents, chunks, and the ingestion pipeline audit trail.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the data models for the Retrieval-Augmented Generation (RAG) subsystem:
1. `DocumentStatus`: Lifecycle states of an uploaded file (`pending`, `processing`,
   `indexed`, `failed`, `deleted`).
2. `PipelineStage` & `PIPELINE_ORDER`: The ordered 10-stage ingestion workflow:
   `upload` -> `validation` -> `extraction` -> `cleaning` -> `metadata` ->
   `chunking` -> `embedding` -> `indexing` -> `quality` -> `completed`.
3. `KnowledgeBase`: Container for collections of related training documents.
4. `Document`: Represents a source file (PDF, DOCX, CSV) with storage path and status.
5. `DocumentChunk`: Text slices with token count, page number, and `vector_id`
   (linking the relational PostgreSQL row to the embedding vector in ChromaDB).
6. `PipelineRun`: Granular audit and retry unit recording each stage execution.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `rag/pipeline.py`: Executes the 10-stage pipeline, updates `Document` status,
  inserts `PipelineRun` rows, and populates `DocumentChunk` records.
- `rag/retrieval.py`: Queries `DocumentChunk` for PostgreSQL keyword searches.
- `rag/vectorstore.py`: ChromaDB stores embeddings with IDs matching `DocumentChunk.vector_id`.
- `routers/knowledge.py`: Serves file uploads and SSE pipeline progress streams.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantEntity

# -----------------------------------------------------------------------------
# Enums and Pipeline Constants
# -----------------------------------------------------------------------------


class DocumentStatus(str, enum.Enum):
    """High-level processing state of a document."""

    pending = "pending"  # Uploaded, waiting for worker to claim
    processing = "processing"  # Actively running through the 10-stage pipeline
    indexed = "indexed"  # Successfully parsed, chunked, embedded, and searchable
    failed = "failed"  # Pipeline failed on a stage (inspect error column)
    deleted = "deleted"  # Soft-deleted / scheduled for cleanup


class PipelineStage(str, enum.Enum):
    """The 10 discrete stages of the document ingestion pipeline.

    Each stage is idempotent and retryable from `rag/pipeline.py`.
    """

    upload = "upload"  # 1. Store raw file bytes on disk
    validation = "validation"  # 2. Inspect magic bytes & verify SHA-256 checksum
    extraction = "extraction"  # 3. Parse raw text from PDF, DOCX, CSV, Markdown
    cleaning = "cleaning"  # 4. Unicode normalization, fix hyphenated words, strip noise
    metadata = "metadata"  # 5. Extract language, word count, document title
    chunking = "chunking"  # 6. Sentence-aware token budgeting (e.g. 700 tokens + 100 overlap)
    embedding = "embedding"  # 7. Generate dense vectors via AI Gateway (text-embedding-3-small)
    indexing = "indexing"  # 8. Upsert vectors to ChromaDB (`org_{hex}`) and chunks to PostgreSQL
    quality = "quality"  # 9. Quality gate (reject empty chunks or high failure rates)
    completed = "completed"  # 10. Mark document indexed and update knowledge base statistics


# Strict execution sequence of the ingestion pipeline
PIPELINE_ORDER: list[PipelineStage] = list(PipelineStage)

_enum = lambda e, n: SAEnum(  # noqa: E731
    e, native_enum=False, length=n, values_callable=lambda x: [m.value for m in x]
)


# -----------------------------------------------------------------------------
# Database Models
# -----------------------------------------------------------------------------


class KnowledgeBase(TenantEntity):
    """A logical knowledge base container (e.g. 'Employee Handbook' or 'Sales Training')."""

    __tablename__ = "knowledge_bases"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    chunk_size_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(TenantEntity):
    """Represents an uploaded source file processed by the system."""

    __tablename__ = "documents"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, 20), default=DocumentStatus.pending, nullable=False
    )
    stage: Mapped[PipelineStage] = mapped_column(
        _enum(PipelineStage, 20), default=PipelineStage.upload, nullable=False
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    word_count: Mapped[int | None] = mapped_column(Integer)
    detected_language: Mapped[str | None] = mapped_column(String(16))
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    runs: Mapped[list[PipelineRun]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="PipelineRun.started_at",
        lazy="selectin",
    )


class DocumentChunk(TenantEntity):
    """Text chunk and metadata stored in PostgreSQL.

    The actual 1536-dimensional vector embedding is stored in ChromaDB
    keyed by `vector_id` format: `"{document_id}:{position}"`.
    """

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "position", name="uq_chunk_doc_position"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(400))
    vector_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


class PipelineRun(TenantEntity):
    """Execution audit trail for an individual stage attempt within the ingestion pipeline.

    Allows tracking execution duration in milliseconds, error messages, and retry attempts.
    """

    __tablename__ = "pipeline_runs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[PipelineStage] = mapped_column(_enum(PipelineStage, 20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "running" | "succeeded" | "failed"
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="runs")
