"""Root API Router aggregation.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file creates the master `api_router` and mounts all feature sub-routers:
- `auth`: Login, refresh, registration, password reset.
- `organizations`, `workspaces`: Tenant and workspace management.
- `learners`: Student directory, profiles, and enrollment progress.
- `knowledge`, `documents`: RAG knowledge bases and file upload pipelines.
- `rag`: Direct hybrid RAG query endpoints.
- `assistant`: Multi-turn conversational learner assistant with function calling.
- `courses`: Curriculum management, AI course generation, and lesson content.
- `assessments`, `submissions`, `evaluations`: Quiz generation, student taking, and auto-grading.
- `workflows`, `executions`: Automation workflows, template provisioning, and execution streaming.
- `tickets`: Support ticket triage and AI drafting.
- `analytics`: Multi-dimensional metrics (learning, AI costs, workflows, knowledge base usage).
- `platform` (keys, audit, jobs, integrations): API keys, audit trail, background jobs, system
health.
- `webhooks`: Inbound signed n8n callbacks and custom integrations.

===============================================================================
HOW IT CONNECTS TO THE APPLICATION:
===============================================================================
- Mounted by `main.py` onto `app.include_router(api_router, prefix=settings.api_v1_prefix)`.
"""

from fastapi import APIRouter

from . import (
    analytics,
    assessments,
    assistant,
    auth,
    courses,
    knowledge,
    learners,
    organizations,
    platform,
    rag,
    tickets,
    webhooks,
    workflows,
)

api_router = APIRouter()

# Authentication & Tenancy
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(organizations.workspaces)

# Learning Domain
api_router.include_router(learners.router)
api_router.include_router(knowledge.router)
api_router.include_router(knowledge.documents_router)
api_router.include_router(rag.router)
api_router.include_router(assistant.router)
api_router.include_router(courses.router)
api_router.include_router(assessments.router)
api_router.include_router(assessments.submissions_router)
api_router.include_router(assessments.evaluations_router)

# Automation & Platform
api_router.include_router(workflows.router)
api_router.include_router(workflows.executions_router)
api_router.include_router(tickets.router)
api_router.include_router(analytics.router)
api_router.include_router(platform.keys_router)
api_router.include_router(platform.audit_router)
api_router.include_router(platform.jobs_router)
api_router.include_router(platform.integrations_router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
