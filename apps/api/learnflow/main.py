"""Application entry point and FastAPI initialization.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file is the main executable entry point for the FastAPI backend. It:
1. Initializes structured logging on startup.
2. Defines the `lifespan` context manager to gracefully drain Redis and PostgreSQL pools on
shutdown.
3. Instantiates the `FastAPI` app with metadata and OpenAPI documentation configuration.
4. Registers the ASGI middleware stack in precise execution order.
5. Installs global JSON error handlers.
6. Mounts all API route modules (`routers/api_router`) under `/api/v1`.
7. Implements `/health` (liveness probe) and `/health/ready` (readiness probe) for Kubernetes.

===============================================================================
STARTUP & INITIALIZATION ORDER:
===============================================================================
Command: `uvicorn learnflow.main:app --host 0.0.0.0 --port 8000`
  1. `logging.configure_logging()` sets format (JSON in prod, console in dev).
  2. `FastAPI` instance created with OpenAPI config.
  3. Middlewares attached (evaluated inside-out / bottom-up in ASGI):
     `RequestContextMiddleware` (outermost)
       ──► `RateLimitMiddleware`
         ──► `UnitOfWorkMiddleware`
           ──► `CORSMiddleware`
             ──► Router handlers (`/api/v1/...`)
  4. Global exception handlers registered via `install_error_handlers(app)`.
  5. `lifespan` executes: logs startup metadata, yields to handle incoming traffic,
     and on termination closes Redis pools and disposes SQLAlchemy engines.
"""

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

# 1. Initialize logging before any requests arrive
configure_logging(json_output=settings.environment != "development")
log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI Lifespan Context Manager.

    Code before `yield` runs at server boot.
    Code after `yield` runs when server receives SIGTERM / SIGINT shutdown signals.
    """
    log.info("startup", environment=settings.environment, llm_provider=settings.llm_provider)
    yield
    # Shutdown sequence: drain network and connection pools cleanly
    await close_redis()
    await dispose_engine()
    log.info("shutdown")


# 2. Instantiate the FastAPI Application
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

# 3. Register Middleware Stack (Executed in reverse order of addition in ASGI)
# Outermost -> RequestContextMiddleware (assigns UUID)
# Next -> RateLimitMiddleware (increments Redis key)
# Next -> UnitOfWorkMiddleware (manages DB commit)
# Innermost -> CORSMiddleware (handles browser preflight)
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

# 4. Attach Global Exception Handlers
install_error_handlers(app)

# 5. Mount API Routers
app.include_router(api_router, prefix=settings.api_v1_prefix)


# -----------------------------------------------------------------------------
# Kubernetes & Monitoring Health Probes
# -----------------------------------------------------------------------------


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Kubernetes Liveness Probe: Returns 200 OK if the process is responsive.

    If this endpoint hangs or fails, Kubernetes restarts the container pod.
    """
    return {"status": "ok", "version": app.version}


@app.get("/health/ready", tags=["ops"], summary="Readiness probe (checks Postgres and Redis)")
async def ready(response: Response) -> dict[str, object]:
    """Kubernetes Readiness Probe: Verifies live connectivity to datastores.

    Checks:
    1. PostgreSQL: Executes `SELECT 1` via `session_scope()`.
    2. Redis: Pings Redis if configured (`redis_enabled()`).

    Returns:
        HTTP 200 if all active datastores are healthy.
        HTTP 503 if any datastore is degraded (tells Kubernetes Ingress to stop routing traffic).
    """
    from sqlalchemy import text

    from .db import session_scope

    checks: dict[str, str] = {}

    # Check 1: PostgreSQL Connection
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    # Check 2: Redis Connection
    if not redis_enabled():
        # Single-node mode: Redis absence is intentional configuration, not an error
        checks["redis"] = "disabled"
    else:
        try:
            await get_redis().ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"

    healthy = all(v in ("ok", "disabled") for v in checks.values())
    if not healthy:
        response.status_code = 503  # Must return 503 so Kubernetes stops traffic routing
    return {"status": "ok" if healthy else "degraded", "checks": checks}
