"""Request id / access log / rate limit middleware."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .config import settings
from .logging import get_logger
from .redis_client import get_redis

log = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window counter per principal-or-IP.

    ponytail: fixed window, not sliding — a burst can straddle the boundary. Swap for a
    token bucket in Lua if abuse becomes real.
    """

    EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(self.EXEMPT_PREFIXES):
            return await call_next(request)

        identity = (
            request.headers.get("X-API-Key")
            or request.headers.get("Authorization")
            or (request.client.host if request.client else "anonymous")
        )
        bucket = f"rl:{hash(identity) & 0xFFFFFFFF}:{int(time.time() // 60)}"
        try:
            redis = get_redis()
            count = await redis.incr(bucket)
            if count == 1:
                await redis.expire(bucket, 90)
        except Exception:  # Redis down must not take the API down with it
            log.warning("rate_limit_unavailable")
            return await call_next(request)

        if count > settings.rate_limit_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests",
                        "details": {"limit_per_minute": settings.rate_limit_per_minute},
                    },
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={"Retry-After": "60"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, settings.rate_limit_per_minute - count)
        )
        return response
