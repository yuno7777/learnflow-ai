"""Learner-facing assistant (tool calling) and the content transformation workflow."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from ..ai.base import ChatMessage
from ..ai.gateway import AIGateway
from ..ai.prompts import LEARNER_ASSISTANT
from ..ai.tools import TOOL_DEFINITIONS, ToolBox
from ..deps import PrincipalDep, RepoDep, SessionDep
from ..models import Conversation, Learner, Message
from ..schemas.knowledge import MessageOut
from ..services import jobs

router = APIRouter(tags=["assistant"])

MAX_TOOL_ROUNDS = 4
OUTPUT_TYPES = [
    "short_summary",
    "detailed_notes",
    "flashcards",
    "faq",
    "quiz",
    "presentation_outline",
    "social_post",
    "email_announcement",
]


class AssistantAsk(BaseModel):
    question: str = Field(min_length=2, max_length=4000)
    learner_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


class AssistantReply(BaseModel):
    answer: str
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


@router.post(
    "/assistant/ask",
    response_model=AssistantReply,
    summary="Ask the learner assistant (searches the KB, reads progress, can raise a ticket)",
)
async def ask(
    body: AssistantAsk,
    repo: RepoDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> AssistantReply:
    gateway = AIGateway(
        session,
        repo.organization_id,
        user_id=principal.user.id if principal.user else None,
        request_id=getattr(request.state, "request_id", None),
    )
    learner = await repo.get(Learner, body.learner_id) if body.learner_id else None
    conversation = (
        await repo.get(Conversation, body.conversation_id)
        if body.conversation_id
        else repo.add(
            Conversation(
                title=body.question[:120],
                kind="learner_assistant",
                learner_id=body.learner_id,
                user_id=principal.user.id if principal.user else None,
            )
        )
    )
    await repo.flush()
    repo.add(Message(conversation_id=conversation.id, role="user", content=body.question))

    toolbox = ToolBox(session, gateway, repo.organization_id, learner)
    system, user = LEARNER_ASSISTANT.render(question=body.question)
    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    executed: list[dict[str, Any]] = []

    # Ground the first turn unconditionally: the assistant should never answer from the model's
    # own priors when the organization's own material is available.
    grounding = await toolbox.call("search_knowledge_base", {"query": body.question, "top_k": 5})
    executed.append({"name": "search_knowledge_base", "arguments": {"query": body.question}})
    messages.append(
        ChatMessage(
            "user",
            "Knowledge base results (use these; cite the source names):\n"
            + json.dumps(grounding, ensure_ascii=False)[:8000],
        )
    )

    result = None
    for _ in range(MAX_TOOL_ROUNDS):
        result = await gateway.complete(
            "learner_assistant",
            messages,
            prompt=LEARNER_ASSISTANT,
            tools=TOOL_DEFINITIONS,
            temperature=0.2,
            max_tokens=1200,
            retrieved_chunk_ids=[c["chunk_id"] for c in toolbox.citations],
        )
        if not result.tool_calls:
            break
        messages.append(ChatMessage("assistant", result.text or ""))
        for call in result.tool_calls:
            output = await toolbox.call(call.name, call.arguments)
            executed.append({"name": call.name, "arguments": call.arguments})
            messages.append(
                ChatMessage(
                    "tool",
                    json.dumps(output, default=str, ensure_ascii=False)[:8000],
                    tool_call_id=call.id,
                    name=call.name,
                )
            )

    assert result is not None
    assistant_message = repo.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=result.text,
            citations=toolbox.citations,
            tool_calls=executed,
            confidence=grounding.get("confidence", 0.0),
            llm_call_id=result.call_id,
        )
    )
    await repo.flush()
    return AssistantReply(
        answer=result.text,
        conversation_id=conversation.id,
        message_id=assistant_message.id,
        tool_calls=executed,
        citations=toolbox.citations,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
    )


@router.get("/assistant/messages/{conversation_id}", response_model=list[MessageOut])
async def history(conversation_id: uuid.UUID, repo: RepoDep) -> list[Message]:
    conversation = await repo.get(Conversation, conversation_id)
    return conversation.messages


class TransformRequest(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    output_types: list[str] = Field(min_length=1, max_length=8)
    instructions: str = Field(default="", max_length=2000)


@router.post(
    "/content/transform",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Turn training material into summaries, flashcards, FAQs, posts and more",
)
async def transform(body: TransformRequest, repo: RepoDep, session: SessionDep) -> dict[str, Any]:
    unknown = [t for t in body.output_types if t not in OUTPUT_TYPES]
    if unknown:
        from ..errors import ValidationFailed

        raise ValidationFailed("Unknown output type", {"unknown": unknown, "allowed": OUTPUT_TYPES})

    queued = []
    for output_type in body.output_types:
        job = await jobs.enqueue(
            session,
            repo.organization_id,
            "transform_content",
            idempotency_key=f"transform:{output_type}:{uuid.uuid4().hex[:10]}",
            resource_type="content_transform",
            document_ids=[str(d) for d in body.document_ids],
            output_type=output_type,
            instructions=body.instructions,
        )
        queued.append({"output_type": output_type, "job_id": str(job.id), "status": job.status})
    return {"jobs": queued}


@router.get("/content/output-types", summary="Supported transformation outputs")
async def output_types() -> list[str]:
    return OUTPUT_TYPES
