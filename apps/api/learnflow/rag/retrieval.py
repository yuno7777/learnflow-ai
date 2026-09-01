"""Hybrid retrieval and grounded RAG answer generation.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the retrieval and question-answering core of LearnFlow AI:
1. `retrieve()`: Executes hybrid retrieval combining:
   - Dense semantic vector search (ChromaDB cosine query).
   - Exact keyword search (PostgreSQL `document_chunks.content ILIKE %term%`).
   - Fuses both rankings using **Reciprocal Rank Fusion** (RRF $k=60$).
2. Hard Refusal Threshold (`min_score=0.25`): If no chunks pass the minimum similarity
   threshold, the system halts immediately and returns a standard refusal.
   (Zero tokens spent calling the generation model!).
3. `build_context()`: Formats retrieved passages into numbered citation blocks:
   `[1] (employee_handbook.pdf, page 4, section: Leave Policy) ...`
4. `answer_question()`: Passes formatted context to `RAG_ANSWER` prompt and invokes `AIGateway`.

===============================================================================
WHY RECIPROCAL RANK FUSION (RRF) OVER PURE DENSE SEARCH?
===============================================================================
Dense embeddings excel at conceptual meaning but frequently fail on exact alphanumeric
tokens (e.g. error code `ERR_4019`, policy code `HR-POL-2026`, product SKUs).
By combining dense embeddings with exact keyword queries, RRF ensures exact matches
surface to the top of the citation ranking.
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

# Damping constant for Reciprocal Rank Fusion formula
RRF_K = 60

# Standardized refusal string returned when no relevant context is found
REFUSAL = (
    "I don't have enough information in this knowledge base to answer that. "
    "Try rephrasing, or upload a document that covers it."
)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}")


@dataclass(slots=True)
class Citation:
    """A citation passage returned to the user with exact document and page metadata."""
    chunk_id: str
    document_id: str
    document_name: str
    snippet: str
    score: float
    page: int | None = None
    section: str | None = None


@dataclass(slots=True)
class Retrieval:
    """The complete result of a hybrid retrieval pass."""
    citations: list[Citation]
    confidence: float
    latency_ms: int
    chunk_ids: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.citations


@dataclass(slots=True)
class Answer:
    """The final grounded answer with citation metadata and AI execution metrics."""
    text: str
    refused: bool
    retrieval: Retrieval
    result: AIResult | None


def _keywords(query: str) -> list[str]:
    """Extracts non-stopword alphanumeric search terms from the query."""
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
    """Queries PostgreSQL `document_chunks` for exact keyword substring matches (`ILIKE`)."""
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
    """Fuses dense vector results and keyword match IDs using Reciprocal Rank Fusion.

    Formula:
        Score = sum( 1 / (60 + rank_in_list) )
    """
    scores: dict[str, float] = {}
    by_id: dict[str, Match] = {}
    for rank, match in enumerate(dense):
        scores[match.id] = scores.get(match.id, 0.0) + 1.0 / (RRF_K + rank + 1)
        by_id[match.id] = match
    for rank, cid in enumerate(keyword_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)
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
    """Performs hybrid dense+keyword retrieval against the organization's knowledge base.

    Execution Flow:
    1. Embeds query text into a vector using `gateway.embed()`.
    2. Queries ChromaDB for top dense vector matches in `org_<hex>` collection.
    3. Queries PostgreSQL `document_chunks` for keyword matches.
    4. Merges scores via Reciprocal Rank Fusion (`_fuse`).
    5. Filters out chunks below `min_score` (default 0.25).
    6. Joins matching chunks with `documents` table to retrieve filenames and page numbers.
    """
    started = time.perf_counter()
    top_k = top_k or settings.retrieval_top_k
    min_score = settings.retrieval_min_score if min_score is None else min_score

    # 1. Generate query embedding
    vectors, _ = await gateway.embed([query], operation="rag_query")
    where: dict[str, Any] = {}
    if knowledge_base_id:
        where["knowledge_base_id"] = str(knowledge_base_id)
    if document_ids:
        where["document_id"] = [str(d) for d in document_ids]

    # 2. Parallel Dense and Keyword searches
    dense = await get_vector_store().query(
        organization_id, vectors[0], top_k=top_k * 2, where=where or None
    )
    keyword_ids = await _keyword_matches(
        session, organization_id, query, knowledge_base_id, document_ids, top_k * 2
    )

    # 3. Fuse rankings and enforce minimum score threshold
    fused = [m for m in _fuse(dense, keyword_ids) if m.score >= min_score][:top_k]

    # 4. Hydrate citation metadata from PostgreSQL
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
                continue
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
    """Formats citations into a numbered block of text with source document and page headers."""
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
    """Executes full Grounded RAG Question Answering pipeline."""
    # 1. Retrieve relevant passages
    retrieval = await retrieve(
        session,
        gateway,
        question,
        organization_id=organization_id,
        knowledge_base_id=knowledge_base_id,
        document_ids=document_ids,
    )
    
    # 2. Hard refusal gate: if no chunks found, return refusal with ZERO token cost
    if retrieval.is_empty:
        return Answer(text=REFUSAL, refused=True, retrieval=retrieval, result=None)

    # 3. Render prompt with retrieved citations
    system, user = RAG_ANSWER.render(context=build_context(retrieval.citations), question=question)
    messages = [ChatMessage("system", system), *(history or []), ChatMessage("user", user)]
    
    # 4. Generate answer using AI Gateway
    result = await gateway.complete(
        "rag_answer",
        messages,
        prompt=RAG_ANSWER,
        temperature=0.1,
        retrieved_chunk_ids=retrieval.chunk_ids,
    )
    refused = "don't have enough information" in result.text.lower()
    return Answer(text=result.text, refused=refused, retrieval=retrieval, result=result)
