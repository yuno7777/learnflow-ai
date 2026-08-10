"""Request id / access log / rate limit middleware.

These are pure ASGI middleware, not `BaseHTTPMiddleware`, and that is deliberate.
`BaseHTTPMiddleware` wraps the response in a streaming task, which defers the exit code of
`yield` dependencies until after the body has been sent — so `get_session`'s commit could
land *after* the client already had the response. A client that registers and immediately
uses its new token would then be told the user does not exist. Pure ASGI middleware keeps
dependency teardown inside the request.
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


class RequestContextMiddleware:
    """Assigns a request id, echoes it on the response and logs one line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        # Starlette exposes scope["state"] as request.state.
        scope.setdefault("state", {})["request_id"] = request_id

        path = scope.get("path", "")
        method = scope.get("method", "")
        structlog.contextvars.bind_contextvars(request_id=request_id, path=path)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "request",
                method=method,
                path=path,
                status=status_code,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            structlog.contextvars.clear_contextvars()


class UnitOfWorkMiddleware:
    """Owns one database transaction per request, and commits it *before* the response leaves.

    FastAPI runs the exit code of `yield` dependencies after the response has been sent, so a
    commit placed there is not visible to a client that immediately issues a follow-up request.
    Committing here — while intercepting `http.response.start`, before any bytes reach the
    client — makes read-after-write hold for API consumers.
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
            if message["type"] == "http.response.start" and not finalised:
                finalised = True
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("commit_failed")
                    # Nothing has been sent yet, so the success status can still be replaced.
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


class RateLimitMiddleware:
    """Fixed-window counter per principal-or-IP.

    ponytail: fixed window, not sliding — a burst can straddle the boundary. Swap for a
    token bucket in Lua if abuse becomes real.
    """

    EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not redis_enabled():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
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
        bucket = f"rl:{hash(identity) & 0xFFFFFFFF}:{int(time.time() // 60)}"

        try:
            redis = get_redis()
            count = await redis.incr(bucket)
            if count == 1:
                await redis.expire(bucket, 90)
        except Exception:  # Redis down must not take the API down with it
            log.warning("rate_limit_unavailable")
            await self.app(scope, receive, send)
            return

        if count > settings.rate_limit_per_minute:
            await self._reject(scope, send)
            return

        remaining = str(max(0, settings.rate_limit_per_minute - count))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.append("X-RateLimit-Limit", str(settings.rate_limit_per_minute))
                response_headers.append("X-RateLimit-Remaining", remaining)
            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _reject(self, scope: Scope, send: Send) -> None:
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
