"""Function calling tool definitions and runtime dispatcher for the Learner Assistant.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file defines the tools that the Learner Assistant can invoke during multi-turn chat:
1. `TOOL_DEFINITIONS`: JSON schemas passed to the LLM describing available tools:
   - `search_knowledge_base`: Retrieves grounded citations from the RAG knowledge base.
   - `get_learner_progress`: Queries active course enrollments and completion percentages.
   - `get_course_info`: Inspects module and lesson curricula.
   - `create_support_ticket`: Escalates unresolved questions to human staff.
   - `recommend_content`: Suggests uncompleted lessons for the student.
2. `ToolBox`: The execution class that handles tool dispatching, interacts with the
   database, and collects citation metadata to attach to the final assistant message.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `routers/assistant.py`: Passes `TOOL_DEFINITIONS` to `AIGateway.complete()`. When the
  model emits `tool_calls`, `ToolBox.call()` executes the tool, returns the result
  back to the model, and loops until a final natural language answer is generated.
- `rag/retrieval.py`: `ToolBox._search_knowledge_base()` calls `retrieve()`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Course, Enrollment, Learner, Lesson, Module, SupportTicket
from ..rag.retrieval import retrieve

# OpenAI-compatible function calling schemas exposed to the model
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the organization's training material for relevant passages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_learner_progress",
            "description": "Current course progress and completion status for this learner.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_course_info",
            "description": "Module and lesson structure of a course the learner is enrolled in.",
            "parameters": {
                "type": "object",
                "properties": {"course_id": {"type": "string"}},
                "required": ["course_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_support_ticket",
            "description": "Escalate to a human when the material cannot answer the question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recommend_content",
            "description": "Suggest the next lessons the learner has not completed yet.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer"}}},
        },
    },
]


class ToolBox:
    """Tool dispatcher executing assistant tool calls against the database and RAG engine."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: Any,
        organization_id: uuid.UUID,
        learner: Learner | None,
    ) -> None:
        self.session = session
        self.gateway = gateway
        self.organization_id = organization_id
        self.learner = learner
        # Collects citations during tool execution to attach to the final chat message
        self.citations: list[dict[str, Any]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatches tool call by name to internal handler method."""
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool '{name}'"}
        return await handler(**arguments)

    async def _search_knowledge_base(self, query: str, top_k: int = 5) -> dict[str, Any]:
        """Performs hybrid dense+keyword search and accumulates citations."""
        retrieval = await retrieve(
            self.session,
            self.gateway,
            query,
            organization_id=self.organization_id,
            top_k=min(max(top_k, 1), 10),
        )
        self.citations.extend(
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_name": c.document_name,
                "snippet": c.snippet,
                "score": c.score,
                "page": c.page,
                "section": c.section,
            }
            for c in retrieval.citations
        )
        return {
            "confidence": retrieval.confidence,
            "passages": [
                {"source": c.document_name, "page": c.page, "text": c.snippet}
                for c in retrieval.citations
            ],
        }

    async def _get_learner_progress(self) -> dict[str, Any]:
        """Queries enrollment progress for the active learner."""
        if self.learner is None:
            return {"error": "No learner is attached to this conversation"}
        rows = (
            await self.session.execute(
                select(Enrollment, Course.title)
                .join(Course, Course.id == Enrollment.course_id)
                .where(Enrollment.learner_id == self.learner.id)
            )
        ).all()
        return {
            "learner": self.learner.full_name,
            "courses": [
                {
                    "course_id": str(e.course_id),
                    "title": title,
                    "progress_percent": round(e.progress * 100),
                    "status": e.status,
                }
                for e, title in rows
            ],
        }

    async def _get_course_info(self, course_id: str) -> dict[str, Any]:
        """Returns the module and lesson curriculum of a course."""
        course = await self.session.get(Course, uuid.UUID(course_id))
        if course is None or course.organization_id != self.organization_id:
            return {"error": "Course not found"}
        modules = (
            (
                await self.session.execute(
                    select(Module).where(Module.course_id == course.id).order_by(Module.position)
                )
            )
            .scalars()
            .all()
        )
        return {
            "title": course.title,
            "description": course.description,
            "modules": [
                {"title": m.title, "summary": m.summary, "objectives": m.learning_objectives}
                for m in modules
            ],
        }

    async def _create_support_ticket(self, subject: str, body: str) -> dict[str, Any]:
        """Inserts a new support ticket row when the assistant cannot resolve an inquiry."""
        ticket = SupportTicket(
            organization_id=self.organization_id,
            subject=subject[:300],
            body=body[:20000],
            learner_id=self.learner.id if self.learner else None,
            status="open",
        )
        self.session.add(ticket)
        await self.session.flush()
        return {"ticket_id": str(ticket.id), "status": "open"}

    async def _recommend_content(self, limit: int = 3) -> dict[str, Any]:
        """Finds next uncompleted lessons in the learner's enrolled courses."""
        if self.learner is None:
            return {"error": "No learner is attached to this conversation"}
        enrollments = (
            (
                await self.session.execute(
                    select(Enrollment).where(Enrollment.learner_id == self.learner.id)
                )
            )
            .scalars()
            .all()
        )
        recommendations: list[dict[str, str]] = []
        for enrollment in enrollments:
            done = {str(x) for x in enrollment.completed_lesson_ids}
            lessons = (
                await self.session.execute(
                    select(Lesson, Module.title)
                    .join(Module, Module.id == Lesson.module_id)
                    .where(Module.course_id == enrollment.course_id)
                    .order_by(Module.position, Lesson.position)
                )
            ).all()
            for lesson, module_title in lessons:
                if str(lesson.id) not in done:
                    recommendations.append(
                        {"module": module_title, "lesson": lesson.title, "why": "Not completed yet"}
                    )
                if len(recommendations) >= limit:
                    break
            if len(recommendations) >= limit:
                break
        return {"recommendations": recommendations}
