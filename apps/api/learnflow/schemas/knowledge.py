from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ..models import DocumentStatus, PipelineStage
from .common import ORMModel


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str | None = None
    workspace_id: UUID | None = None
    chunk_size_tokens: int = Field(default=700, ge=100, le=2000)
    chunk_overlap_tokens: int = Field(default=100, ge=0, le=500)


class KnowledgeBaseOut(ORMModel):
    id: UUID
    name: str
    description: str | None
    workspace_id: UUID | None
    embedding_model: str
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    document_count: int
    chunk_count: int
    created_at: datetime


class PipelineRunOut(ORMModel):
    id: UUID
    stage: PipelineStage
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None
    details: dict[str, Any]


class DocumentOut(ORMModel):
    id: UUID
    knowledge_base_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    stage: PipelineStage
    progress: float
    error: str | None
    page_count: int | None
    word_count: int | None
    detected_language: str | None
    doc_metadata: dict[str, Any]
    indexed_at: datetime | None
    created_at: datetime


class DocumentDetail(DocumentOut):
    chunk_count: int = 0
    runs: list[PipelineRunOut] = Field(default_factory=list)
    job_id: UUID | None = None


class ChunkOut(ORMModel):
    id: UUID
    position: int
    content: str
    token_count: int
    page_number: int | None
    section: str | None


class CitationOut(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    snippet: str
    score: float
    page: int | None = None
    section: str | None = None


class RagQuery(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    knowledge_base_id: UUID | None = None
    document_ids: list[UUID] | None = None
    conversation_id: UUID | None = None
    top_k: int = Field(default=6, ge=1, le=20)


class RagAnswer(BaseModel):
    answer: str
    refused: bool
    citations: list[CitationOut]
    confidence: float
    model: str | None
    provider: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    retrieval_latency_ms: int
    llm_latency_ms: int
    conversation_id: UUID | None = None
    message_id: UUID | None = None


class MessageOut(ORMModel):
    id: UUID
    role: str
    content: str
    citations: list[Any]
    tool_calls: list[Any]
    confidence: float | None
    refused: bool
    feedback: int | None
    created_at: datetime


class ConversationOut(ORMModel):
    id: UUID
    title: str
    kind: str
    knowledge_base_id: UUID | None
    learner_id: UUID | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1, description="-1 unhelpful, 1 helpful")
