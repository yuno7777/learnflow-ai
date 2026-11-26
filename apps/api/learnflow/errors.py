"""Standardized error handling and domain exception hierarchy.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file establishes a single, consistent JSON error format across all endpoints:
```json
{
  "error": {
    "code": "not_found",
    "message": "Course not found",
    "details": {}
  },
  "request_id": "8f3b21..."
}
```
It defines:
1. Domain exception classes (`NotFound`, `Forbidden`, `Unauthorized`, `ProviderError`,
   `TenantIsolationError`, `ValidationFailed`, etc.).
2. Global FastAPI exception handlers in `install_error_handlers(app)` that catch both
   domain exceptions and unexpected crashes, formatting them cleanly and ensuring
   internal database constraints/SQL details are NEVER leaked to callers.

===============================================================================
WHY THIS FILE EXISTS & WHAT HAPPENS IF REMOVED:
===============================================================================
If removed:
- Endpoints throwing exceptions would return disparate error formats (HTML error pages,
  raw stack traces, unformatted strings, or default FastAPI shapes).
- Frontend API clients (`apps/web/lib/api.ts`) would fail to parse structured error codes.
- Sensitive backend internals (SQL statements, column names) would leak in production.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `learnflow/main.py`: Calls `install_error_handlers(app)` during startup.
- `routers/*.py`: Raise `NotFound`, `Forbidden`, `ValidationFailed` on invalid client operations.
- `services/repository.py`: Raises `TenantIsolationError` if code attempts cross-tenant access.
- `ai/gateway.py`: Raises `ProviderError` when upstream LLMs (OpenAI/Azure) fail.
- `apps/web/lib/api.ts`: Reads the `error.code`, `error.message`, and `request_id` fields.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging import get_logger

log = get_logger(__name__)


# -----------------------------------------------------------------------------
# Base & Domain Exceptions Hierarchy
# -----------------------------------------------------------------------------


class AppError(Exception):
    """Base class for all operational and domain exceptions in LearnFlow.

    Subclasses define their default HTTP `status_code` and string machine `code`.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(AppError):
    """Resource not found (or hidden due to tenant isolation). Status 404."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Conflict(AppError):
    """Resource already exists (e.g. duplicate organization slug or email). Status 409."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class Unauthorized(AppError):
    """Missing or invalid authentication credentials (bad JWT or API key). Status 401."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class Forbidden(AppError):
    """Authenticated caller lacks required RBAC permissions or membership. Status 403."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ValidationFailed(AppError):
    """Business validation failed (e.g. invalid quiz questions or bad magic bytes). Status 422."""

    status_code = 422
    code = "validation_failed"


class RateLimited(AppError):
    """Client has exceeded the allowed requests-per-minute threshold. Status 429."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class ProviderError(AppError):
    """Upstream AI (OpenAI, Azure) or automation service failed after retries. Status 502."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"


class TenantIsolationError(AppError):
    """Raised when a query would cross an organization boundary without explicit tenant scoping.

    CRITICAL NOTE: This indicates an internal programming bug (not a user error).
    Always returns 403 Forbidden and logs as a critical error.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "tenant_isolation"


# -----------------------------------------------------------------------------
# Error Payload Serializer & Global Handlers
# -----------------------------------------------------------------------------


def _payload(code: str, message: str, details: dict[str, Any], request: Request) -> dict[str, Any]:
    """Helper to construct the standardized JSON error envelope with the current request_id."""
    return {
        "error": {"code": code, "message": message, "details": details},
        "request_id": getattr(request.state, "request_id", None),
    }


def install_error_handlers(app: FastAPI) -> None:
    """Registers global exception handlers on the FastAPI application instance.

    Called once during application initialization in `learnflow/main.py`.
    """

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        """Handles custom domain exceptions (`NotFound`, `Forbidden`, `ValidationFailed`, etc.)."""
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.details, request),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Handles FastAPI/Pydantic request body validation failures (HTTP 422)."""
        return JSONResponse(
            status_code=422,
            content=_payload(
                "validation_failed", "Request validation failed", {"errors": exc.errors()}, request
            ),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        """Handles database constraint violations (e.g. duplicate unique key).

        SECURITY DECISION:
        We NEVER leak raw PostgreSQL error messages, constraint names, or table names
        to the client. We log the details internally and return a clean 409 Conflict.
        """
        log.warning("integrity_error", error=str(exc.orig))
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload("conflict", "Resource conflicts with existing data", {}, request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Handles standard HTTP exceptions raised by Starlette/FastAPI internals (e.g. 404
        router misses).
        """
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code, str(exc.detail), {}, request),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Catches any unexpected Python runtime crashes (HTTP 500).

        Logs full traceback with the request ID, but returns a safe generic error to the caller.
        """
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "Internal server error", {}, request),
        )
