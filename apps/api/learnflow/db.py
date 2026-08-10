"""Async engine + session factory. One engine per process."""

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

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        kwargs: dict = {"echo": settings.debug, "pool_pre_ping": True}
        if settings.database_url.startswith("sqlite"):
            kwargs.pop("pool_pre_ping")
        _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: the request's session, owned by UnitOfWorkMiddleware.

    The commit deliberately does *not* live here. FastAPI runs the exit code of a `yield`
    dependency **after the response has been sent**, so committing here means a client can
    hold a 201 whose row is not yet visible — register, then immediately use the token, and
    be told the user does not exist. The middleware commits before the response headers go
    out instead; this dependency only hands out the session it created.
    """
    session: AsyncSession | None = request.scope.get("state", {}).get("db_session")
    if session is not None:
        yield session
        return

    # No middleware in the stack (direct ASGI calls, some tests): fall back to owning the
    # transaction here, accepting the late-commit caveat above.
    async with get_sessionmaker()() as fallback:
        try:
            yield fallback
            await fallback.commit()
        except Exception:
            await fallback.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Same contract as get_session, for workers and scripts."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
