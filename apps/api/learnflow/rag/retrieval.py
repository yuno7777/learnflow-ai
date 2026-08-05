"""Hybrid retrieval + grounded answering.

Retrieval fuses dense vector search with a keyword pass over chunk text (reciprocal rank
fusion). Dense search alone misses exact identifiers — policy numbers, error codes, product
SKUs — which is exactly what people search internal training material for.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway, AIResult
from ..ai.prompts import RAG_ANSWER
from ..config import settings
from ..models import Document, DocumentChunk
from .vectorstore import Match, get_vector_store

RRF_K = 60  # standard reciprocal-rank-fusion damping constant
REFUSAL = (
    "I don't have enough information in this knowledge base to answer that. "
    "Try rephrasing, or upload a document that covers it."
)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}")


@dataclass(slots=True)
class Citation:
    chunk_id: str
    document_id: str
    document_name: str
    snippet: str
    score: float
    page: int | None = None
    section: str | None = None


@dataclass(slots=True)
class Retrieval:
    citations: list[Citation]
    confidence: float
    latency_ms: int
    chunk_ids: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.citations


@dataclass(slots=True)
class Answer:
    text: str
    refused: bool
    retrieval: Retrieval
    result: AIResult | None


def _keywords(query: str) -> list[str]:
    stop = {"the", "and", "for", "what", "how", "does", "with", "from", "are", "was", "you"}
    seen: list[str] = []
    for word in _WORD.findall(query.lower()):
        if word not in stop and word not in seen:
            seen.append(word)
    return seen[:8]


async def _keyword_matches(
    session: AsyncSession,
    organization_id: uuid.UUID,
    query: str,
    knowledge_base_id: uuid.UUID | None,
    document_ids: list[uuid.UUID] | None,
    limit: int,
) -> list[str]:
    terms = _keywords(query)
    if not terms:
        return []
    stmt = select(DocumentChunk.vector_id).where(
        DocumentChunk.organization_id == organization_id,
        or_(*[DocumentChunk.content.ilike(f"%{t}%") for t in terms]),
    )
    if knowledge_base_id:
        stmt = stmt.where(DocumentChunk.knowledge_base_id == knowledge_base_id)
    if document_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))
    return list((await session.execute(stmt.limit(limit))).scalars().all())


def _fuse(dense: list[Match], keyword_ids: list[str]) -> list[Match]:
    scores: dict[str, float] = {}
    by_id: dict[str, Match] = {}
    for rank, match in enumerate(dense):
        scores[match.id] = scores.get(match.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        by_id[match.id] = match
    for rank, cid in enumerate(keyword_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
    # Keyword-only hits have no dense payload; they are dropped rather than fetched again —
    # a term match with zero semantic similarity is noise more often than signal.
    return [by_id[cid] for cid in ranked if cid in by_id]


async def retrieve(
    session: AsyncSession,
    gateway: AIGateway,
    query: str,
    *,
    organization_id: uuid.UUID,
    knowledge_base_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> Retrieval:
    started = time.perf_counter()
    top_k = top_k or settings.retrieval_top_k
    min_score = settings.retrieval_min_score if min_score is None else min_score

    vectors, _ = await gateway.embed([query], operation="rag_query")
    where: dict[str, Any] = {}
    if knowledge_base_id:
        where["knowledge_base_id"] = str(knowledge_base_id)
    if document_ids:
        where["document_id"] = [str(d) for d in document_ids]

    dense = await get_vector_store().query(
        organization_id, vectors[0], top_k=top_k * 2, where=where or None
    )
    keyword_ids = await _keyword_matches(
        session, organization_id, query, knowledge_base_id, document_ids, top_k * 2
    )
    fused = [m for m in _fuse(dense, keyword_ids) if m.score >= min_score][:top_k]

    citations: list[Citation] = []
    if fused:
        rows = (
            await session.execute(
                select(DocumentChunk, Document.filename)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.organization_id == organization_id,
                    DocumentChunk.vector_id.in_([m.id for m in fused]),
                )
            )
        ).all()
        by_vector = {chunk.vector_id: (chunk, filename) for chunk, filename in rows}
        for match in fused:
            entry = by_vector.get(match.id)
            if entry is None:
                continue  # vector store ahead of Postgres (mid re-index) — skip, don't cite blind
            chunk, filename = entry
            citations.append(
                Citation(
                    chunk_id=str(chunk.id),
                    document_id=str(chunk.document_id),
                    document_name=filename,
                    snippet=chunk.content[:600],
                    score=round(match.score, 4),
                    page=chunk.page_number,
                    section=chunk.section,
                )
            )

    confidence = round(sum(c.score for c in citations[:3]) / max(1, len(citations[:3])), 4)
    return Retrieval(
        citations=citations,
        confidence=confidence if citations else 0.0,
        latency_ms=int((time.perf_counter() - started) * 1000),
        chunk_ids=[c.chunk_id for c in citations],
    )


def build_context(citations: list[Citation]) -> str:
    return "\n\n".join(
        f"[{i}] ({c.document_name}"
        + (f", page {c.page}" if c.page else "")
        + (f", section: {c.section}" if c.section else "")
        + f")\n{c.snippet}"
        for i, c in enumerate(citations, start=1)
    )


async def answer_question(
    session: AsyncSession,
    gateway: AIGateway,
    question: str,
    *,
    organization_id: uuid.UUID,
    knowledge_base_id: uuid.UUID | None = None,
    document_ids: list[uuid.UUID] | None = None,
    history: list[ChatMessage] | None = None,
) -> Answer:
    retrieval = await retrieve(
        session,
        gateway,
        question,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_ids=document_ids,
    )
    if retrieval.is_empty:
        # Refuse without spending a token — the model has nothing to ground an answer in.
        return Answer(text=REFUSAL, refused=True, retrieval=retrieval, result=None)

    system, user = RAG_ANSWER.render(context=build_context(retrieval.citations), question=question)
    messages = [ChatMessage("system", system), *(history or []), ChatMessage("user", user)]
    result = await gateway.complete(
        "rag_answer",
        messages,
        prompt=RAG_ANSWER,
        temperature=0.1,
        retrieved_chunk_ids=retrieval.chunk_ids,
    )
    refused = "don't have enough information" in result.text.lower()
    return Answer(text=result.text, refused=refused, retrieval=retrieval, result=result)
