# Testing

## Running

```bash
# Backend — no Postgres, Redis, Chroma or API key needed
cd apps/api
.venv/bin/pytest -q
.venv/bin/pytest tests/test_ingestion_and_rag.py -v
.venv/bin/ruff check learnflow tests && .venv/bin/mypy learnflow

# Frontend
cd apps/web
npm test && npm run typecheck && npm run lint

# End to end — needs a running, seeded stack
docker compose up -d && docker compose exec api python -m learnflow.seed
cd apps/web && E2E_RUN=1 npx playwright test
```

## How the backend suite avoids infrastructure

| Real dependency | In tests |
|---|---|
| PostgreSQL | SQLite via aiosqlite, schema created from the same models |
| ChromaDB | `InMemoryVectorStore` with identical semantics |
| OpenAI | `MockLLM` / `MockEmbeddings` — deterministic, schema-valid, no network |
| Redis | A null client, exercising the documented degrade-open paths |
| ARQ worker | Enqueues are captured; tests invoke the task function directly |
| n8n | Not configured, so the built-in runner executes; one test patches the client to prove the hand-off |

The mock provider matters more than it sounds. It is not a stub returning fixed strings: given
a JSON schema it resolves `$ref`, honours `pattern`, `minLength` and `minItems`, and
synthesises a **valid** instance — so the structured-output path, including the repair branch,
is genuinely exercised. A coherence pass makes generated quiz items internally consistent, so
the validator has real items to accept and reject rather than only ever rejecting.

Mock embeddings are a hashed bag-of-words projection, so overlapping text really does score
higher in cosine similarity. That is what makes `test_rag_answer_is_cited_and_grounded`
meaningful: it asserts the correct chunk — the one containing `RW-2291` — is retrieved.

## What is covered

**Authentication and tenancy** (`test_auth_and_tenancy.py`, 11 tests)
Registration creates an org_admin. Wrong password and unknown account return an identical
error. Refresh tokens are single use. A learner role is refused learner management. A
cross-tenant read returns 404 and a cross-tenant list returns zero. The repository refuses a
non-tenant model and stamps the tenant on writes. API keys authenticate, never re-expose the
raw value, and stop working when revoked.

**Ingestion and RAG** (`test_ingestion_and_rag.py`, 11 tests)
Chunking respects the token budget and produces real overlap. Cleaning joins hyphen-broken
words. Uploads are rejected on mismatched magic bytes, unsupported extensions and size. The
full ten-stage pipeline runs in order with every stage succeeding. Duplicate uploads 409. A
failing stage is **recorded, not swallowed** — the document ends `failed` with the error
persisted even though the task raises. Answers carry citations naming the right document and
containing the right text. Nothing relevant means refusal with zero output tokens. A second
organization querying the same question gets nothing. Deleting a document removes its vectors.
Conversation history persists across turns.

**Generation and evaluation** (`test_generation_and_evaluation.py`, 11 tests)
Quiz validation rejects answers not in the options, ambiguous options, duplicates, missing
rubrics, low confidence and unsupported source references. Generated courses land in `review`
with modules, lessons and the prompt version recorded. Publishing an empty course is a 409.
Lessons can be edited then published. The answer key is hidden from the learner view.
Objective questions are graded by rules at confidence 1.0. A low-confidence evaluation becomes
`needs_review`, appears in the queue, and can only be finalised by a human review call.

**Workflows and webhooks** (`test_workflows_and_webhooks.py`, 11 tests)
Signature round-trip; tampering, a wrong secret, a malformed header and a stale timestamp all
raise. Adding a learner fires the onboarding workflow, which runs six steps end to end, sends
the email through the mock adapter, writes the checklist and attributes its token cost. A
workflow failure is recorded with its error and can be retried. Triggering twice with the same
idempotency key produces one execution. With a webhook path configured, the run hands off to
n8n and stays open. A signed callback closes it out and attaches n8n's steps — and replaying
that exact body returns 401. An unsigned callback is refused. An inbound external event
dispatches a workflow. Ticket triage classifies and escalates. Workflow analytics reflect the
runs.

**Seed and analytics** (`test_seed_and_analytics.py`, 5 tests)
The seed loads a complete organization and is idempotent. Analytics report real numbers,
including seeded failures. An empty organization reports zeros and nulls, **not fabricated
data**. Audit entries record privileged actions with a request id and no secrets.

**Frontend** (10 tests)
Formatters distinguish a missing metric from a genuine zero. Status badges map to the right
tone. Progress bars expose accessible values and clamp out-of-range input. Error states
surface the real message instead of swallowing it.

## CI

Six jobs (`.github/workflows/ci.yml`):

1. **backend** — ruff lint, ruff format check, mypy, pytest
2. **frontend** — eslint, tsc, vitest, next build
3. **integration** — real Postgres and Redis services; migrations up, seed, then `downgrade base` to prove they reverse
4. **security** — pip-audit, bandit, npm audit, and a grep that fails the build on a committed credential-shaped literal
5. **docker** — validates `docker compose config`, builds all three images, boots the API image and hits `/health` and `/openapi.json`
6. **manifests** — regenerates the k8s manifests and n8n workflows and fails if they drift from their generators; `kubectl kustomize` dry-run

Deployment is a separate gated job that runs only on `main` and only when deploy secrets
exist; otherwise it logs a notice and exits cleanly.

## Adding a test

Backend fixtures live in `tests/conftest.py`: `client` (authenticated HTTP client),
`org_a` / `org_b` (two tenants — use both whenever isolation is in play), `vector_store`,
`queued_jobs` (captures enqueues) and `session`.

```python
async def test_something(client: AsyncClient, org_a: dict) -> None:
    response = await client.post("/api/v1/learners", headers=auth(org_a), json={...})
    assert response.status_code == 201
```

To exercise background work, capture the job and call the task directly:

```python
jobs = (await client.get("/api/v1/jobs?kind=ingest_document", headers=auth(org_a))).json()
await ingest_document({}, jobs["items"][0]["id"], org_a["organization_id"], document_id=doc_id)
```
