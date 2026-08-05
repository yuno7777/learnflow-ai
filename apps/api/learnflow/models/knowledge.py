"""Knowledge bases, uploaded documents, chunks and the ingestion pipeline audit trail."""

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


class DocumentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    indexed = "indexed"
    failed = "failed"
    deleted = "deleted"


class PipelineStage(str, enum.Enum):
    upload = "upload"
    validation = "validation"
    extraction = "extraction"
    cleaning = "cleaning"
    metadata = "metadata"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    quality = "quality"
    completed = "completed"


PIPELINE_ORDER: list[PipelineStage] = list(PipelineStage)

_enum = lambda e, n: SAEnum(  # noqa: E731
    e, native_enum=False, length=n, values_callable=lambda x: [m.value for m in x]
)


class KnowledgeBase(TenantEntity):
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
    """Chunk metadata + text. The embedding itself lives in Chroma keyed by `vector_id`."""

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
    """One row per pipeline stage attempt — the retryable unit of ingestion."""

    __tablename__ = "pipeline_runs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[PipelineStage] = mapped_column(_enum(PipelineStage, 20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running|succeeded|failed
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="runs")
