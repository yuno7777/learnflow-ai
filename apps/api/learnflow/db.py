"""Async database engine and session factory module.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file manages the asynchronous PostgreSQL (or SQLite in tests) connection engine,
the SQLAlchemy session factory (`async_sessionmaker`), and provides session
dependency functions for FastAPI request handlers (`get_session`) and background
workers/scripts (`session_scope`).

===============================================================================
WHY THIS FILE EXISTS & WHAT HAPPENS IF REMOVED:
===============================================================================
Without this file:
1. No database queries would be possible across the entire application.
2. Connection pooling would break, leading to connection exhaustion under load.
3. FastAPI routes and ARQ background workers would have no unified way to obtain
   transactional database sessions.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `config.py`: Supplies `settings.database_url` (e.g. `postgresql+asyncpg://...`).
- `middleware.py` (`UnitOfWorkMiddleware`):
  Calls `get_sessionmaker()()` at the start of each HTTP request, attaches the
  resulting session to `request.state.db_session`, and commits the transaction
  BEFORE sending HTTP response headers.
- `deps.py`: Uses `get_session` as the `SessionDep` dependency in FastAPI endpoints.
- `services/repository.py` (`TenantRepo`): Wraps the `AsyncSession` passed from `deps.py`.
- `worker/tasks.py` & `seed.py`: Uses `@asynccontextmanager session_scope()` to obtain
  sessions outside of HTTP request cycles.
- `main.py` & `worker/main.py`: Calls `dispose_engine()` during shutdown to close connection pools.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings

# Global singletons: one engine and one sessionmaker per Python process.
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Creates or returns the global SQLAlchemy `AsyncEngine` singleton.

    Features:
    - `pool_pre_ping=True`: Tests whether a connection from the pool is still alive
      before issuing a query (prevents broken pipe errors after database restarts).
    - `echo=settings.debug`: Prints raw SQL queries to stdout when in debug mode.
    """
    global _engine
    if _engine is None:
        kwargs: dict = {"echo": settings.debug, "pool_pre_ping": True}
        if settings.database_url.startswith("sqlite"):
            # SQLite does not support connection pool pre-pinging.
            kwargs.pop("pool_pre_ping")
        _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Creates or returns the global session factory singleton.

    Configuration:
    - `expire_on_commit=False`: Prevents SQLAlchemy from expiring object attributes
      after a commit (allows returning models in responses without triggering extra queries).
    - `autoflush=False`: Gives developers explicit control over when queries flush to the DB.
    - `class_=AsyncSession`: Configures modern async/await execution for all operations.
    """
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI Request Dependency: provides the database session for the current request.

    ===========================================================================
    CRITICAL ARCHITECTURAL DECISION:
    ===========================================================================
    Why does the `commit()` NOT live inside this dependency?
    
    FastAPI runs the cleanup/exit code of `yield` dependencies AFTER the HTTP response
    body has already been streamed to the client network socket.
    
    If `commit()` were placed here:
    1. A user registers via `POST /api/v1/auth/register`.
    2. FastAPI sends `201 Created` with a new JWT token to the client.
    3. The client receives the response and immediately fires `GET /api/v1/learners/me`.
    4. But the database commit from step 1 has NOT executed yet!
    5. The second request fails with `404 User Not Found` (a race condition).

    SOLUTION:
    `UnitOfWorkMiddleware` (in `middleware.py`) creates the session, stores it on
    `request.scope["state"]["db_session"]`, and commits the transaction BEFORE sending
    response headers. This `get_session` dependency simply hands out that session.
    """
    # 1. Primary path: Retrieve the active session initialized by UnitOfWorkMiddleware
    session: AsyncSession | None = request.scope.get("state", {}).get("db_session")
    if session is not None:
        yield session
        return

    # 2. Fallback path: If middleware is absent (e.g. during specific unit tests or direct ASGI calls),
    # own the session lifecycle directly here.
    async with get_sessionmaker()() as fallback:
        try:
            yield fallback
            await fallback.commit()
        except Exception:
            await fallback.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for standalone tasks, worker jobs, and CLI scripts.

    Usage:
        async with session_scope() as session:
            result = await session.execute(select(User))

    Automatically commits on success or rolls back if an unhandled exception occurs.
    Used by: `learnflow/worker/tasks.py`, `learnflow/seed.py`, `worker/main.py`.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Closes all active database connection pools gracefully.

    Called during application shutdown in `learnflow/main.py` (`lifespan`) and
    `learnflow/worker/main.py` (`shutdown`).
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
