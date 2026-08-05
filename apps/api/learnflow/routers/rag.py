"""RAG assistant: grounded question answering plus conversation history."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request, status

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway
from ..deps import PrincipalDep, RepoDep, SessionDep, require
from ..models import Conversation, Message
from ..rag.retrieval import answer_question
from ..schemas.common import Page, PageParams, page_params
from ..schemas.knowledge import (
    CitationOut,
    ConversationDetail,
    ConversationOut,
    FeedbackIn,
    MessageOut,
    RagAnswer,
    RagQuery,
)

router = APIRouter(tags=["rag"])

HISTORY_TURNS = 6


@router.post(
    "/rag/query",
    response_model=RagAnswer,
    dependencies=[Depends(require("rag:query"))],
    summary="Ask the knowledge base a question and get a cited answer",
)
async def query(
    body: RagQuery,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> RagAnswer:
    gateway = AIGateway(
        session,
        repo.organization_id,
        user_id=principal.user.id if principal.user else None,
        request_id=getattr(request.state, "request_id", None),
    )

    conversation = (
        await repo.get(Conversation, body.conversation_id) if body.conversation_id else None
    )
    history: list[ChatMessage] = []
    if conversation is not None:
        recent = sorted(conversation.messages, key=lambda m: m.created_at)[-HISTORY_TURNS:]
        history = [ChatMessage(role=m.role, content=m.content) for m in recent]
    else:
        conversation = repo.add(
            Conversation(
                title=body.question[:120],
                kind="admin_rag",
                knowledge_base_id=body.knowledge_base_id,
                user_id=principal.user.id if principal.user else None,
            )
        )
        await repo.flush()

    repo.add(Message(conversation_id=conversation.id, role="user", content=body.question))

    result = await answer_question(
        session,
        gateway,
        body.question,
        organization_id=repo.organization_id,
        knowledge_base_id=body.knowledge_base_id,
        document_ids=body.document_ids,
        history=history,
    )
    citations = [CitationOut(**asdict(c)) for c in result.retrieval.citations]
    assistant = repo.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=result.text,
            citations=[c.model_dump() for c in citations],
            confidence=result.retrieval.confidence,
            refused=result.refused,
            llm_call_id=result.result.call_id if result.result else None,
        )
    )
    await repo.flush()

    return RagAnswer(
        answer=result.text,
        refused=result.refused,
        citations=citations,
        confidence=result.retrieval.confidence,
        model=result.result.model if result.result else None,
        provider=result.result.provider if result.result else None,
        input_tokens=result.result.input_tokens if result.result else 0,
        output_tokens=result.result.output_tokens if result.result else 0,
        estimated_cost_usd=result.result.estimated_cost_usd if result.result else 0.0,
        retrieval_latency_ms=result.retrieval.latency_ms,
        llm_latency_ms=result.result.latency_ms if result.result else 0,
        conversation_id=conversation.id,
        message_id=assistant.id,
    )


@router.get("/conversations", response_model=Page[ConversationOut], summary="List conversations")
async def list_conversations(
    repo: RepoDep, params: PageParams = Depends(page_params)
) -> Page[ConversationOut]:
    items, total = await repo.list(
        Conversation, sort=params.sort, limit=params.limit, offset=params.offset
    )
    return Page(
        items=[ConversationOut.model_validate(i) for i in items],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Conversation with full message history",
)
async def get_conversation(conversation_id: uuid.UUID, repo: RepoDep) -> ConversationDetail:
    conversation = await repo.get(Conversation, conversation_id)
    detail = ConversationDetail.model_validate(conversation)
    detail.messages = [MessageOut.model_validate(m) for m in conversation.messages]
    return detail


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rate an assistant answer (feeds AI observability)",
)
async def rate_message(
    conversation_id: uuid.UUID, message_id: uuid.UUID, body: FeedbackIn, repo: RepoDep
) -> None:
    await repo.get(Conversation, conversation_id)
    message = await repo.get(Message, message_id)
    message.feedback = body.rating
    await repo.flush()
