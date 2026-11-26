"""Learner Assistant API router with multi-turn tool calling and content transformations.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file powers the interactive AI chatbot and content repurposing features:
1. `POST /assistant/ask`: Multi-turn conversational learner assistant:
   - Injects unconditional knowledge base grounding pass on turn 1.
   - Iterates up to `MAX_TOOL_ROUNDS = 4` executing tool calls via `ToolBox`
     (`search_knowledge_base`, `get_learner_progress`, `get_course_info`, `create_support_ticket`,
     `recommend_content`).
   - Accumulates citations and appends complete message history to PostgreSQL.
2. `GET /assistant/messages/{id}`: Retrieves full chat conversation history.
3. `POST /content/transform`: Generates flashcards, FAQs, executive summaries, or quiz items
   from indexed documents as asynchronous background jobs.

===============================================================================
MULTI-TURN FUNCTION CALLING LOOP:
===============================================================================
User Question
  ──► Unconditional RAG Grounding (`search_knowledge_base`)
  ──► AI Gateway Completion with `TOOL_DEFINITIONS`
  ──► If Model emits `tool_calls`:
      1. `ToolBox.call(name, args)`
      2. Append `ChatMessage("tool", output)`
      3. Loop back to AI Gateway
  ──► Return final grounded response + collected citation metadata.
"""

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
from ..deps import PrincipalDep, RepoDep, SessionDep, scoped_learner_id
from ..models import Conversation, Learner, Message
from ..schemas.knowledge import MessageOut
from ..services import jobs

router = APIRouter(tags=["assistant"])

MAX_TOOL_ROUNDS = 4  # Maximum sequential function call iterations before forcing response
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
    """Payload schema for asking a question to the learner assistant."""

    question: str = Field(min_length=2, max_length=4000)
    learner_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


class AssistantReply(BaseModel):
    """Response payload returned by the learner assistant."""

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
    """Interacts with the AI assistant, executing function calling tools and returning citations."""
    gateway = AIGateway(
        session,
        repo.organization_id,
        user_id=principal.user.id if principal.user else None,
        request_id=getattr(request.state, "request_id", None),
    )
    learner_id = scoped_learner_id(principal, body.learner_id)
    learner = await repo.get(Learner, learner_id) if learner_id else None

    # 1. Fetch or create conversation thread
    conversation = (
        await repo.get(Conversation, body.conversation_id)
        if body.conversation_id
        else repo.add(
            Conversation(
                title=body.question[:120],
                kind="learner_assistant",
                learner_id=learner_id,
                user_id=principal.user.id if principal.user else None,
            )
        )
    )
    await repo.flush()
    repo.add(Message(conversation_id=conversation.id, role="user", content=body.question))

    # 2. Render prompt and execute initial grounding pass
    toolbox = ToolBox(session, gateway, repo.organization_id, learner)
    system, user = LEARNER_ASSISTANT.render(question=body.question)
    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    executed: list[dict[str, Any]] = []

    # Unconditional RAG grounding query
    grounding = await toolbox.call("search_knowledge_base", {"query": body.question, "top_k": 5})
    executed.append({"name": "search_knowledge_base", "arguments": {"query": body.question}})
    messages.append(
        ChatMessage(
            "user",
            "Knowledge base results (use these; cite the source names):\n"
            + json.dumps(grounding, ensure_ascii=False)[:8000],
        )
    )

    # 3. Multi-turn Tool Calling Loop
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
            break  # Model is ready with its final natural language answer

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
    # 4. Save assistant response and citations to database
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
    """Returns full chronological chat history for a conversation."""
    conversation = await repo.get(Conversation, conversation_id)
    return conversation.messages


class TransformRequest(BaseModel):
    """Payload schema for requesting asynchronous document content transformations."""

    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    output_types: list[str] = Field(min_length=1, max_length=8)
    instructions: str = Field(default="", max_length=2000)


@router.post(
    "/content/transform",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Turn training material into summaries, flashcards, FAQs, posts and more",
)
async def transform(body: TransformRequest, repo: RepoDep, session: SessionDep) -> dict[str, Any]:
    """Enqueues async content transformation tasks (e.g. FAQs, flashcards, summaries)."""
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
    """Lists available content transformation formats."""
    return OUTPUT_TYPES
