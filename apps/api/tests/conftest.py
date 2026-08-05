"""Test harness: SQLite + in-memory vector store + mock AI providers + inline job runner.

No Postgres, no Redis, no Chroma, no paid API calls. Background jobs are captured and run
explicitly by the tests that care, which keeps assertions about async work deterministic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from learnflow import db as db_module
from learnflow.config import settings
from learnflow.models import Base, Role
from learnflow.rag.vectorstore import InMemoryVectorStore, set_vector_store
from learnflow.services import jobs as job_service


@pytest.fixture(scope="session", autouse=True)
def configure_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    settings.llm_provider = "mock"
    settings.embedding_provider = "mock"
    settings.llm_model = "mock"
    settings.embedding_model = "mock-embed"
    settings.jwt_secret = "test-secret-value-long-enough-for-hs256-abc"
    settings.webhook_signing_secret = "test-webhook-secret"
    settings.upload_dir = str(tmp_path_factory.mktemp("uploads"))
    settings.rate_limit_per_minute = 100000


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[Any]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker_fixture(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(sessionmaker_fixture) -> AsyncIterator[Any]:
    async with sessionmaker_fixture() as s:
        yield s
        await s.commit()


@pytest.fixture
def vector_store() -> AsyncIterator[InMemoryVectorStore]:
    store = InMemoryVectorStore()
    set_vector_store(store)
    yield store
    set_vector_store(None)


@pytest.fixture
def queued_jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture enqueue calls instead of talking to Redis; tests run the task themselves."""
    captured: list[dict[str, Any]] = []

    class _FakeArq:
        async def enqueue_job(self, name, *args, _job_id=None, **kwargs):
            captured.append({"name": name, "args": args, "kwargs": kwargs, "job_id": _job_id})

            class _Handle:
                job_id = _job_id

            return _Handle()

    async def _get_arq():
        return _FakeArq()

    monkeypatch.setattr(job_service, "get_arq", _get_arq)
    return captured


@pytest.fixture
async def client(
    sessionmaker_fixture, vector_store, queued_jobs, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(db_module, "get_sessionmaker", lambda: sessionmaker_fixture)

    # Redis is only used for rate limiting and SSE fan-out here; a null client keeps the
    # middleware on its documented "degrade open" path without a running server.
    class _NullRedis:
        async def incr(self, *_a, **_k):
            raise RuntimeError("redis disabled in tests")

        async def publish(self, *_a, **_k):
            return 0

        async def ping(self):
            return False

    from learnflow import middleware, redis_client
    from learnflow.automation import engine as engine_mod
    from learnflow.rag import pipeline as pipeline_mod

    monkeypatch.setattr(redis_client, "get_redis", lambda: _NullRedis())
    monkeypatch.setattr(middleware, "get_redis", lambda: _NullRedis())
    monkeypatch.setattr(engine_mod, "get_redis", lambda: _NullRedis())
    monkeypatch.setattr(pipeline_mod, "get_redis", lambda: _NullRedis())

    from learnflow.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


async def register(client: AsyncClient, email: str, org: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Sup3rSecret!pass",
            "full_name": "Test Admin",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.fixture
async def org_a(client: AsyncClient) -> dict[str, Any]:
    return await register(client, f"a-{uuid.uuid4().hex[:6]}@example.com", "Acme Training")


@pytest.fixture
async def org_b(client: AsyncClient) -> dict[str, Any]:
    return await register(client, f"b-{uuid.uuid4().hex[:6]}@example.com", "Globex Learning")


@pytest.fixture
def role_all() -> list[Role]:
    return list(Role)
