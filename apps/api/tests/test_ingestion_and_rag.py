"""Document ingestion pipeline, tenant-isolated vector search and grounded answering."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from learnflow.rag.chunking import chunk_segments, count_tokens
from learnflow.rag.extract import Segment, clean, validate_upload
from learnflow.worker.tasks import ingest_document

from .conftest import auth

HANDBOOK = b"""# Remote Work Policy

Employees may work remotely up to three days per week. Requests must be approved by a
direct manager at least five business days in advance. The approval code for a remote
request is RW-2291.

## Equipment

The company provides a laptop and a monitor. Equipment must be returned within ten
business days of leaving the company. Damaged equipment is reported to IT via ticket
type EQ-DAMAGE.

## Expenses

Home office expenses up to 400 USD per year are reimbursable with a receipt. Internet
costs are not reimbursable under this policy.
"""


async def _create_kb(client: AsyncClient, tokens: dict) -> str:
    response = await client.post(
        "/api/v1/knowledge-bases",
        headers=auth(tokens),
        json={"name": "Employee Handbook", "chunk_size_tokens": 120, "chunk_overlap_tokens": 20},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _upload(client: AsyncClient, tokens: dict, kb_id: str, data: bytes = HANDBOOK) -> dict:
    response = await client.post(
        f"/api/v1/documents?knowledge_base_id={kb_id}",
        headers=auth(tokens),
        files={"file": ("handbook.md", data, "text/markdown")},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def _ingest(document: dict, tokens: dict) -> dict[str, Any]:
    return await ingest_document(
        {},
        document["job_id"],
        tokens["organization_id"],
        document_id=document["id"],
    )


# ------------------------------------------------------------------ unit level
def test_chunking_respects_budget_and_overlaps() -> None:
    segments = [Segment(text=HANDBOOK.decode(), page=1, section="Policy")]
    chunks = chunk_segments(segments, chunk_size=60, overlap=15)
    assert len(chunks) > 1
    assert all(c.token_count <= 90 for c in chunks)  # budget + one trailing sentence
    assert all(c.page == 1 for c in chunks)
    # Overlap means consecutive chunks share text.
    assert any(
        set(chunks[i].text.split()) & set(chunks[i + 1].text.split())
        for i in range(len(chunks) - 1)
    )


def test_cleaning_joins_hyphen_breaks() -> None:
    assert "reimbursable" in clean("reimburs-\nable")
    assert count_tokens("hello world") >= 2


def test_upload_validation_rejects_mismatched_magic_bytes() -> None:
    from learnflow.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        validate_upload("evil.pdf", "application/pdf", b"not a pdf at all", 1024)
    with pytest.raises(ValidationFailed):
        validate_upload("script.exe", "application/octet-stream", b"MZ", 1024)
    with pytest.raises(ValidationFailed):
        validate_upload("big.txt", "text/plain", b"x" * 100, 10)


# ------------------------------------------------------------------ pipeline
async def test_full_ingestion_pipeline(client: AsyncClient, org_a: dict, vector_store) -> None:
    kb_id = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_id)
    assert document["status"] == "pending"

    result = await _ingest(document, org_a)
    assert result["status"] == "indexed"

    detail = (await client.get(f"/api/v1/documents/{document['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "indexed"
    assert detail["progress"] == 1.0
    assert detail["chunk_count"] > 0
    assert detail["word_count"] > 50
    assert detail["detected_language"] == "en"

    stages = [run["stage"] for run in detail["runs"]]
    assert stages == [
        "upload",
        "validation",
        "extraction",
        "cleaning",
        "metadata",
        "chunking",
        "embedding",
        "indexing",
        "quality",
        "completed",
    ]
    assert all(run["status"] == "succeeded" for run in detail["runs"])
    assert await vector_store.count(__import__("uuid").UUID(org_a["organization_id"])) > 0


async def test_duplicate_upload_is_rejected(client: AsyncClient, org_a: dict) -> None:
    kb_id = await _create_kb(client, org_a)
    await _upload(client, org_a, kb_id)
    response = await client.post(
        f"/api/v1/documents?knowledge_base_id={kb_id}",
        headers=auth(org_a),
        files={"file": ("handbook.md", HANDBOOK, "text/markdown")},
    )
    assert response.status_code == 409


async def test_pipeline_failure_is_recorded_not_swallowed(client: AsyncClient, org_a: dict) -> None:
    kb_id = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_id, data=b"# Title\n\n" + b"x" * 5)

    # File is valid on upload but has nothing usable after cleaning: the cleaning stage fails.
    with pytest.raises(RuntimeError):
        await _ingest(document, org_a)

    detail = (await client.get(f"/api/v1/documents/{document['id']}", headers=auth(org_a))).json()
    assert detail["status"] == "failed"
    assert detail["error"]
    failed = [r for r in detail["runs"] if r["status"] == "failed"]
    assert failed and failed[0]["error"]


# ------------------------------------------------------------------ retrieval
async def test_rag_answer_is_cited_and_grounded(client: AsyncClient, org_a: dict) -> None:
    kb_id = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_id)
    await _ingest(document, org_a)

    response = await client.post(
        "/api/v1/rag/query",
        headers=auth(org_a),
        json={
            "question": "What is the approval code for a remote work request?",
            "knowledge_base_id": kb_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refused"] is False
    assert body["citations"], "an answer must carry citations"
    assert body["citations"][0]["document_name"] == "handbook.md"
    assert body["confidence"] > 0
    assert body["model"] and body["provider"] == "mock"
    assert body["input_tokens"] > 0
    assert body["retrieval_latency_ms"] >= 0
    assert "RW-2291" in " ".join(c["snippet"] for c in body["citations"])


async def test_rag_refuses_when_nothing_relevant(client: AsyncClient, org_a: dict) -> None:
    kb_id = await _create_kb(client, org_a)
    response = await client.post(
        "/api/v1/rag/query",
        headers=auth(org_a),
        json={"question": "What is the tensile strength of titanium?", "knowledge_base_id": kb_id},
    )
    body = response.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert body["output_tokens"] == 0  # refusal costs nothing


async def test_vector_search_is_tenant_isolated(
    client: AsyncClient, org_a: dict, org_b: dict
) -> None:
    kb_a = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_a)
    await _ingest(document, org_a)

    kb_b = await _create_kb(client, org_b)
    response = await client.post(
        "/api/v1/rag/query",
        headers=auth(org_b),
        json={
            "question": "What is the approval code for a remote work request?",
            "knowledge_base_id": kb_b,
        },
    )
    body = response.json()
    assert body["refused"] is True
    assert body["citations"] == []


async def test_document_delete_removes_vectors(
    client: AsyncClient, org_a: dict, vector_store
) -> None:
    import uuid

    kb_id = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_id)
    await _ingest(document, org_a)
    org_uuid = uuid.UUID(org_a["organization_id"])
    assert await vector_store.count(org_uuid) > 0

    deleted = await client.delete(f"/api/v1/documents/{document['id']}", headers=auth(org_a))
    assert deleted.status_code == 204
    assert await vector_store.count(org_uuid) == 0


async def test_conversation_history_is_persisted(client: AsyncClient, org_a: dict) -> None:
    kb_id = await _create_kb(client, org_a)
    document = await _upload(client, org_a, kb_id)
    await _ingest(document, org_a)

    first = (
        await client.post(
            "/api/v1/rag/query",
            headers=auth(org_a),
            json={"question": "How many remote days are allowed?", "knowledge_base_id": kb_id},
        )
    ).json()
    conversation_id = first["conversation_id"]

    await client.post(
        "/api/v1/rag/query",
        headers=auth(org_a),
        json={
            "question": "And who approves it?",
            "knowledge_base_id": kb_id,
            "conversation_id": conversation_id,
        },
    )
    detail = (
        await client.get(f"/api/v1/conversations/{conversation_id}", headers=auth(org_a))
    ).json()
    assert len(detail["messages"]) == 4  # two user turns, two assistant turns
