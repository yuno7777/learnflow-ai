# LearnFlow AI

Multi-tenant AI workflow automation for LMS and enterprise training. Upload training
material, turn it into a citation-backed knowledge base, generate reviewable courses and
assessments from it, grade written answers under human supervision, and automate the
follow-up work through n8n — with token cost, latency and failure rates visible throughout.

```
Next.js 15 (App Router, TS)  ──REST/SSE──►  FastAPI  ──►  PostgreSQL   (tenant-scoped)
                                              │  │  │
                                              │  │  └──►  ChromaDB     (collection per org)
                                              │  └─────►  Redis ──► ARQ worker
                                              └────────►  n8n  (signed webhooks + callbacks)
```

---

## Quick start

```bash
git clone https://github.com/yuno7777/learnflow-ai.git
cd learnflow-ai
python scripts/dev.py up
```

Use `py -3 scripts/dev.py up` in Windows PowerShell or `python3 scripts/dev.py up`
on macOS or Linux if `python` is unavailable. Install Python 3.11+ and Docker Compose v2 first.

Seven services come up: `postgres`, `redis`, `chromadb`, `api`, `worker`, `web`, `n8n`.
Dependency ordering is enforced with real health checks, and migrations run as their own
service before the API starts.

Then load the demo dataset:

```bash
docker compose exec api python -m learnflow.seed
```

| Surface | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| OpenAPI schema | http://localhost:8000/openapi.json |
| n8n | http://localhost:5678 |

**Demo credentials (local development only — created by the seed command):**

```
admin@learnflow.dev         / LearnFlow!2026   organization admin  -> /dashboard
instructor1@learnflow.dev   / LearnFlow!2026   instructor          -> /dashboard
reviewer@learnflow.dev      / LearnFlow!2026   reviewer            -> /dashboard
support@learnflow.dev       / LearnFlow!2026   support agent       -> /dashboard
ana.silva@acme.example.com  / LearnFlow!2026   learner             -> /learn
```

Sign in as the learner to see the other side of the product: their own courses, a lesson
reader that records progress, an assessment to sit, and the feedback once it has been
marked. Login routes by role — a learner never lands on the admin dashboard.

**No API key is required to start.** `LLM_PROVIDER=mock` runs a deterministic offline
provider that synthesises schema-valid structured output and hash-based embeddings, so the
whole platform — including RAG retrieval quality — works end to end with nothing configured.
Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` when you want real models.

---

## What is actually implemented

Everything below is exercised by the test suite (59 backend tests, 10 frontend tests).

### Knowledge base and RAG
- Upload PDF, DOCX, TXT, Markdown, CSV. Files are validated against their **magic bytes**,
  not just their extension.
- A **ten-stage pipeline** — upload, validation, extraction, cleaning, metadata, chunking,
  embedding, indexing, quality, completion — with one row per stage attempt, so a failure is
  inspectable and the run is **resumable from the stage that broke**.
- Sentence-aware chunking with token budgets and carried-forward overlap.
- **Hybrid retrieval**: dense vector search fused with a keyword pass (reciprocal rank
  fusion), because dense search alone misses exact identifiers like policy codes.
- Every answer returns source document, page, section, per-chunk score, retrieval confidence,
  model, provider, token counts, estimated cost and both latencies.
- **The assistant refuses** rather than guessing when nothing clears the similarity
  threshold — and spends zero tokens doing so.
- Live ingestion progress over Server-Sent Events.

### AI layer
- Providers: OpenAI, Azure OpenAI, any OpenAI-compatible endpoint (HF TGI, vLLM), local
  Ollama, and a deterministic mock. Swappable by one environment variable.
- **One choke point** (`ai/gateway.py`) for every model call: retries with backoff, rate-limit
  handling, provider fallback, structured-output validation with one repair attempt, token
  accounting, cost estimation and an observability row per attempt.
- Versioned prompts; the version is written onto every call record, so any historical output
  traces back to the exact text that produced it.

### Course and assessment generation
- Course structure and mixed-format assessments generated from selected documents against
  strict Pydantic schemas.
- Generated questions are additionally validated for **duplicates, ambiguity, answers not in
  the options, missing rubrics and unsupported source references** — rejected items are shown
  with the reason.
- Nothing auto-publishes. Generated courses land in `review`; a human edits and publishes.

### Assessment evaluation with a human gate
- Objective questions are graded **deterministically in code** — no model call, confidence 1.0.
- Free-text answers are graded against a rubric; the model's score is clamped to the
  question's own points.
- Anything below the confidence threshold becomes `needs_review` and appears in a reviewer
  queue. API keys cannot approve an evaluation — a human must sign off.

### Automation
- Five **exportable n8n workflows** in `automation/n8n-workflows/`, driven by HMAC-signed
  webhooks with a freshness window and replay protection (the signature is stored under a
  unique constraint).
- Signed callbacks close out executions and attach the steps n8n actually ran.
- **Built-in runners** execute the same steps when n8n is unavailable, so the demo, the tests
  and CI never depend on a live n8n instance — and the execution row records which path ran.
- Full execution detail: steps, retries, input/output payloads, n8n execution id, token cost,
  manual retry, and live status over SSE.

### The learner side
- Learner records and login accounts are **separate on purpose** — most learners in an LMS are
  imported from an HRIS and never sign in. An admin grants portal access explicitly, which
  creates the account and links it to the learner record.
- A learner login is the only principal that sits *inside* a tenant but must not see all of
  it. That boundary is enforced server-side: a learner sees exactly one learner (themselves),
  only published courses they are enrolled in, only their own submissions, and gets a **404 —
  not a 403** for anyone else's, so ids cannot be probed.
- Submitting as another learner, marking another learner's progress and asking the assistant
  about another learner are each refused, and each has a test.

### Honest analytics
- The seed writes 30 days of usage history so the charts are not empty on first launch.
  Those rows are **flagged**, the API can exclude them (`?exclude_demo=true`), and the UI
  says so with a "real calls only" toggle — seeded history never masquerades as real spend.

### Multi-tenancy and security
- Tenant isolation lives in the **data layer**: the repository refuses to build a query
  against a tenant-scoped model without an organization filter, and stamps the tenant id on
  every write. Cross-tenant reads return 404, indistinguishable from "does not exist".
- Vectors are isolated by **collection per organization**, not by a metadata filter that can
  be forgotten.
- Six roles with a server-side permission map, JWT access tokens, single-use rotating refresh
  tokens, hashed API keys shown exactly once, per-principal rate limiting, audit logging, and
  secret redaction before anything reaches a log sink.

---

## Repository layout

```
learnflow-ai/
├── apps/
│   ├── api/                    FastAPI backend
│   │   ├── learnflow/
│   │   │   ├── ai/             providers, gateway, prompts, structured schemas, tools
│   │   │   ├── automation/     n8n client, engine, runners, events, email adapters
│   │   │   ├── models/         SQLAlchemy 2.0 models by domain
│   │   │   ├── rag/            extraction, chunking, vector store, retrieval, pipeline
│   │   │   ├── routers/        versioned REST API
│   │   │   ├── schemas/        request/response models
│   │   │   ├── services/       repository, jobs, generation, evaluation, audit
│   │   │   └── worker/         ARQ tasks and cron
│   │   ├── alembic/            migrations
│   │   └── tests/              59 backend tests
│   └── web/                    Next.js 15 frontend
│       ├── app/dashboard/      staff dashboard
│       └── app/learn/          learner portal
├── automation/n8n-workflows/   5 importable workflow JSON files
├── infrastructure/kubernetes/  23 manifests + kustomization
├── scripts/                    generators for the manifests and workflows
├── docs/                       architecture, setup, API, RAG, security, deployment…
├── docker-compose.yml
└── .env.example
```

---

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/architecture.md) | System design and six Mermaid diagrams |
| [Local setup](docs/setup.md) | Docker and non-Docker development |
| [Environment variables](docs/environment.md) | Every setting, what it does, what breaks |
| [API](docs/api.md) | Endpoint reference with request/response examples |
| [RAG architecture](docs/rag.md) | Ingestion, chunking, hybrid retrieval, grounding |
| [n8n integration](docs/n8n.md) | Importing workflows, signing, callbacks |
| [Database schema](docs/database.md) | Tables, relationships, tenancy rules |
| [Security](docs/security.md) | Authentication flow, RBAC, webhook signing, threat notes |
| [Deployment](docs/deployment.md) | Docker and Kubernetes |
| [Testing](docs/testing.md) | What is tested and how to run it |
| [Demo walkthrough](docs/demo.md) | The full 15-step demonstration |
| [Limitations](docs/limitations.md) | What this is not, and what would come next |

---

## Development

```bash
# Backend
cd apps/api
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check learnflow tests
.venv/bin/mypy learnflow
.venv/bin/pytest -q

# Frontend
cd apps/web
npm install
npm run lint && npm run typecheck && npm test && npm run build
```

Backend tests need no Postgres, Redis, Chroma or API key: they run on SQLite with an
in-memory vector store, the mock provider and an inline job runner.

Two files are generated — edit the generator, not the output:

```bash
python scripts/build_n8n_workflows.py     # automation/n8n-workflows/*.json
python scripts/build_k8s_manifests.py     # infrastructure/kubernetes/*.yaml
```

CI fails if the generated files drift from their generators.

---

## Screenshots and demo recording

No screenshots are committed. To produce them:

```bash
docker compose up --build
docker compose exec api python -m learnflow.seed
cd apps/web && E2E_RUN=1 npx playwright test --headed
```

Playwright captures a trace on failure (`playwright-report/`). For a recording, follow
[docs/demo.md](docs/demo.md) — the fifteen steps are ordered so each one leaves visible
state for the next.

---

## Licence

MIT.
