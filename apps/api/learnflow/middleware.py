"""Pure ASGI middleware layer: Request correlation, UnitOfWork, and Rate Limiting.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file implements the low-level ASGI middleware stack that wraps every HTTP
request entering the FastAPI application:
1. `RequestContextMiddleware`: Generates or extracts `X-Request-ID`, binds it
   to Structlog context variables, attaches `request.state.request_id`, echoes it
   in response headers, and logs duration/status upon completion.
2. `UnitOfWorkMiddleware`: Manages the database session lifecycle for each request,
   intercepting `http.response.start` to commit the transaction BEFORE any response
   bytes leave the server.
3. `RateLimitMiddleware`: Fixed-window rate limiter using atomic Redis increments
   (`INCR`) per minute bucket, returning HTTP 429 when limits are exceeded.

===============================================================================
WHY PURE ASGI MIDDLEWARE INSTEAD OF Starlette `BaseHTTPMiddleware`?
===============================================================================
Starlette's `BaseHTTPMiddleware` executes request processing inside an async
streaming generator. In FastAPI, `yield` dependencies (such as DB sessions)
run their exit/teardown code *after* the entire response body has been flushed.

This creates a serious race condition:
If a user registers (`POST /auth/register`), receives a `201 Created` response with a
token, and immediately calls `GET /learners/me`, the database transaction from the
first request may not have committed yet! The second request fails with 404.

By writing pure ASGI middleware (`__call__(scope, receive, send)`), we intercept
the low-level `http.response.start` message and explicitly await `session.commit()`
BEFORE the first byte of response headers is transmitted to the client.

===============================================================================
MIDDLEWARE EXECUTION ORDER (Configured in `learnflow/main.py`):
===============================================================================
Inbound Request:
  ──► RequestContextMiddleware (Assigns UUID, starts timer)
  ──► RateLimitMiddleware (Checks Redis counter)
  ──► UnitOfWorkMiddleware (Opens DB session on request.state)
  ──► FastAPI Routers & Business Logic
Outbound Response:
  ◄── UnitOfWorkMiddleware (Commits DB transaction on http.response.start)
  ◄── RateLimitMiddleware (Appends X-RateLimit headers)
  ◄── RequestContextMiddleware (Appends X-Request-ID header, logs request summary)
  ◄── Client Network Socket
"""

from __future__ import annotations

import json
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings
from .logging import get_logger
from .redis_client import get_redis, redis_enabled

log = get_logger("http")


# -----------------------------------------------------------------------------
# 1. Request Context & Access Logging Middleware
# -----------------------------------------------------------------------------


class RequestContextMiddleware:
    """Assigns correlation IDs and logs structured access metrics for each HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Pass non-HTTP scopes (e.g. WebSocket or lifespan events) straight through
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1. Extract existing X-Request-ID from client, or generate a fresh UUID4 hex string
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request_id = headers.get("x-request-id") or uuid.uuid4().hex

        # 2. Attach request_id to Starlette request.state (accessible via request.state.request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        path = scope.get("path", "")
        method = scope.get("method", "")

        # 3. Bind request_id and path to Structlog thread-local contextvars
        structlog.contextvars.bind_contextvars(request_id=request_id, path=path)
        started = time.perf_counter()
        status_code = 500

        # 4. Intercept the outgoing response to append the X-Request-ID header
        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 5. Log final request summary with duration and clean up context variables
            log.info(
                "request",
                method=method,
                path=path,
                status=status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            structlog.contextvars.clear_contextvars()


# -----------------------------------------------------------------------------
# 2. UnitOfWork Database Transaction Middleware
# -----------------------------------------------------------------------------


class UnitOfWorkMiddleware:
    """Guarantees one database transaction per request, committing BEFORE headers leave.

    Data Flow:
    1. Instantiates a new `AsyncSession` from `db.get_sessionmaker()`.
    2. Places the session in `scope["state"]["db_session"]`.
    3. During route execution, `deps.get_session` returns this exact session.
    4. When FastAPI finishes and emits `http.response.start`, this middleware catches it
       and awaits `session.commit()`.
    5. If commit fails, it rolls back and replaces the response with a 500 error before
       any data is sent over the wire.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from .db import get_sessionmaker

        state = scope.setdefault("state", {})
        session = get_sessionmaker()()
        state["db_session"] = session
        finalised = False

        async def send_wrapper(message: Message) -> None:
            nonlocal finalised
            # Intercept response start event to commit transaction prior to sending bytes
            if message["type"] == "http.response.start" and not finalised:
                finalised = True
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("commit_failed")
                    # Replace whatever response was planned with a clean JSON 500
                    body = json.dumps(
                        {
                            "error": {
                                "code": "internal_error",
                                "message": "Could not commit the transaction",
                                "details": {},
                            },
                            "request_id": state.get("request_id"),
                        }
                    ).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    raise
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            if not finalised:
                await session.rollback()
            raise
        finally:
            await session.close()


# -----------------------------------------------------------------------------
# 3. Redis Fixed-Window Rate Limiting Middleware
# -----------------------------------------------------------------------------


class RateLimitMiddleware:
    """Fixed-window rate limiter utilizing atomic Redis increments.

    Key Format: `rl:{identity_hash}:{minute_timestamp}`
    Identity: Resolves `X-API-Key` -> `Authorization` Bearer -> Client IP.
    """

    EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Skip rate limiting for non-HTTP scopes or if Redis is not configured
        if scope["type"] != "http" or not redis_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # Skip rate limiting on health probes and OpenAPI documentation routes
        if path.startswith(self.EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        client = scope.get("client")
        identity = (
            headers.get("x-api-key")
            or headers.get("authorization")
            or (client[0] if client else "anonymous")
        )
        # Create a bucket key tied to the current 1-minute window
        bucket = f"rl:{hash(identity) & 0xFFFFFFFF}:{int(time.time() // 60)}"

        try:
            redis = get_redis()
            count = await redis.incr(bucket)
            if count == 1:
                # Set a 90-second expiration on first access to clean up automatically
                await redis.expire(bucket, 90)
        except Exception:
            # RESILIENCE DECISION: Redis failure must degrade open and NOT crash the API
            log.warning("rate_limit_unavailable")
            await self.app(scope, receive, send)
            return

        # Check if caller exceeded the limit
        if count > settings.rate_limit_per_minute:
            await self._reject(scope, send)
            return

        remaining = str(max(0, settings.rate_limit_per_minute - count))

        # Append RateLimit telemetry headers to the response
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.append("X-RateLimit-Limit", str(settings.rate_limit_per_minute))
                response_headers.append("X-RateLimit-Remaining", remaining)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _reject(self, scope: Scope, send: Send) -> None:
        """Sends a standard HTTP 429 Too Many Requests JSON response."""
        body = json.dumps(
            {
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests",
                    "details": {"limit_per_minute": settings.rate_limit_per_minute},
                },
                "request_id": scope.get("state", {}).get("request_id"),
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", b"60"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
