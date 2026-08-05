"""Conversations plus the AI observability tables (one row per LLM call)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantEntity


class Conversation(TenantEntity):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="admin_rag", nullable=False)
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("knowledge_bases.id", ondelete="SET NULL")
    )
    learner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("learners.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )


class Message(TenantEntity):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|tool|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    tool_calls: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    refused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("llm_calls.id", ondelete="SET NULL")
    )
    feedback: Mapped[int | None] = mapped_column(Integer)  # -1 | 1

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class PromptTemplate(TenantEntity):
    """Versioned prompt. Rendered text is stored on the LLM call so history stays reproducible."""

    __tablename__ = "prompt_templates"
    __table_args__ = (UniqueConstraint("organization_id", "name", "version", name="uq_prompt_ver"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ModelConfiguration(TenantEntity):
    __tablename__ = "model_configurations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), default="chat", nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=0.2, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(120))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LLMCall(TenantEntity):
    """One observability row per provider round-trip, including failed and fallback attempts."""

    __tablename__ = "llm_calls"

    operation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_name: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    schema_validation_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    retrieved_chunk_ids: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    tool_calls: Mapped[list[Any]] = mapped_column(default=list, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    workflow_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_executions.id", ondelete="SET NULL"), index=True
    )


class UsageRecord(TenantEntity):
    """Rolled-up billing view. Written alongside every successful LLM/embedding call."""

    __tablename__ = "usage_records"

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # llm | embedding
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
