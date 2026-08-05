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
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(organizations.workspaces)
api_router.include_router(learners.router)
api_router.include_router(knowledge.router)
api_router.include_router(knowledge.documents_router)
api_router.include_router(rag.router)
api_router.include_router(assistant.router)
api_router.include_router(courses.router)
api_router.include_router(assessments.router)
api_router.include_router(assessments.submissions_router)
api_router.include_router(assessments.evaluations_router)
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
