from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import dispose_engine
from .errors import install_error_handlers
from .logging import configure_logging, get_logger
from .middleware import RateLimitMiddleware, RequestContextMiddleware, UnitOfWorkMiddleware
from .redis_client import close_redis, get_redis, redis_enabled
from .routers import api_router

configure_logging(json_output=settings.environment != "development")
log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", environment=settings.environment, llm_provider=settings.llm_provider)
    yield
    await close_redis()
    await dispose_engine()
    log.info("shutdown")


app = FastAPI(
    title="LearnFlow AI",
    version="0.1.0",
    description=(
        "Multi-tenant AI workflow automation for LMS and enterprise training.\n\n"
        "Authenticate with `Authorization: Bearer <access token>` (plus `X-Organization-Id` to "
        "act on an org other than your default), or with `X-API-Key: lf_...` for machine access."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
)
app.add_middleware(UnitOfWorkMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
install_error_handlers(app)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/health/ready", tags=["ops"], summary="Readiness probe (checks Postgres and Redis)")
async def ready(response: Response) -> dict[str, object]:
    from sqlalchemy import text

    from .db import session_scope

    checks: dict[str, str] = {}
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    if not redis_enabled():
        # Single-node mode. Redis being absent is configuration, not a fault — reporting it
        # as an error would leave the pod permanently unready.
        checks["redis"] = "disabled"
    else:
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
    healthy = all(v in ("ok", "disabled") for v in checks.values())
    if not healthy:
        response.status_code = 503  # k8s must see a failing readiness probe, not a 200
    return {"status": "ok" if healthy else "degraded", "checks": checks}
