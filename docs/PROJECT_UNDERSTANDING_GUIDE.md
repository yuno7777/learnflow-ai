# LearnFlow AI: Master System Design & Technical Interview Guide

---

## 1. Executive Project Summary

LearnFlow AI is a production-oriented, multi-tenant AI workflow automation platform tailored for Learning Management Systems (LMS) and corporate training departments. It ingests diverse enterprise training materials (PDF, DOCX, CSV, TXT, Markdown), validates them against file magic bytes, and runs them through a deterministic 10-stage ingestion pipeline into a citation-backed hybrid vector and keyword knowledge base. From this grounded corpus, the system automatically generates modular course curricula and rigorous assessments with built-in quality guardrails (rejecting ambiguous, duplicate, or hallucinated questions).

When learners submit assessments, objective questions are graded instantly in Python code at zero AI cost, while free-text answers are evaluated by an LLM against rubrics and clamped to assigned point limits; any score falling below a 0.75 confidence threshold is halted in a human-in-the-loop reviewer queue. The entire operational lifecycle—learner onboarding, inactive learner nudging, support ticket triage, and ingestion alerts—is orchestrated via HMAC-signed, replay-protected webhooks connecting to an [n8n](https://n8n.io/) workflow automation cluster with local Python fallback runners. Every single model interaction is gated through a centralized AI Gateway that tracks token usage, calculates per-call USD costs, handles provider failovers, validates structured schemas with repair passes, and writes audit records without leaking multi-tenant data.

---

## 2. What Problem It Solves

### Target Users
* **Enterprise L&D (Learning & Development) Teams & Instructional Designers:** Who spend weeks manually transcribing policy documents and technical manuals into structured training courses and quiz questions.
* **LMS Administrators & Instructors:** Who struggle with grading hundreds of subjective, open-ended assignments consistently while maintaining auditing standards.
* **Corporate Learners:** Who need grounded, interactive, and personalized assistance directly referencing internal proprietary materials without hallucinated answers.
* **Support & Training Ops Teams:** Who need automated student onboarding, proactive engagement nudging for at-risk/inactive learners, and automated helpdesk triage.

### Core Problems Addressed
1. **The Hallucination & Citation Problem in Corporate Training:** Generic LLMs invent facts. LearnFlow enforces strict grounding: if semantic and keyword search fail to clear a similarity threshold ($0.25$), the assistant refuses to answer, spending zero generation tokens. When it answers, every statement links back to the exact chunk ID, document, page number, and section.
2. **The "AI Black Box" Grading Dilemma:** Pure AI auto-grading creates compliance and trust issues. LearnFlow splits grading: deterministic code checks objective questions (multiple choice, true/false) with $1.0$ confidence, while subjective questions are scored by AI with explicit confidence scores. Anything under $75\%$ confidence enters a human reviewer queue where staff can override scores. API keys are strictly forbidden from approving grades—a human user principal is mandatory.
3. **Multi-Tenant Data Leakage:** In multi-tenant SaaS, using a single vector index with metadata filters risks cross-tenant leaks if a developer forgets a `where` clause. LearnFlow partitions vectors into isolated Chroma collections per organization (`org_<uuid.hex>`) and enforces database-level tenant binding via `TenantRepo`.
4. **Fragile Workflow Integrations:** Offloading logic to external workflow engines like n8n often leads to broken callbacks and replay attacks. LearnFlow signs all outbound and inbound webhooks with timestamped HMAC-SHA256 digests, enforces a 300-second freshness window, stores signatures in a unique database index to prevent replays, and includes built-in Python fallback runners if n8n is offline.

---

## 3. Core User Experience

### Primary User Workflows

```
                                  STAFF EXPERIENCE (Admin / Instructor / Reviewer)
┌────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│ Upload Docs    │ ──► │ Ingestion Pipeline   │ ──► │ Course & Quiz Gen    │ ──► │ Human Review Queue   │
│ (PDF/DOCX/CSV) │     │ (10 Stages + SSE UI) │     │ (Pydantic Validation)│     │ (Approve Submissions)│
└────────────────┘     └──────────────────────┘     └──────────────────────┘     └──────────────────────┘
                                                                                            ▲
                                                                                            │ (Low Confidence)
                                  LEARNER EXPERIENCE (/learn)                                │
┌────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐     ┌──────────┴───────────┐
│ Sign In        │ ──► │ Browse Courses &     │ ──► │ Submit Assessments   │ ──► │ Instant Feedback or  │
│ (Self-Scoped)  │     │ Read Lessons         │     │ (Objective & Essay)  │     │ Provisional Status   │
└────────────────┘     └──────────────────────┘     └──────────────────────┘     └──────────────────────┘
```

1. **Document Ingestion & Knowledge Base Construction:**
   * An instructor uploads employee manuals or onboarding guidelines.
   * A 10-stage background pipeline processes the file (validation $\rightarrow$ extraction $\rightarrow$ cleaning $\rightarrow$ metadata $\rightarrow$ chunking $\rightarrow$ embedding $\rightarrow$ indexing $\rightarrow$ quality checks).
   * Live progress streams to the dashboard via Server-Sent Events (SSE).
2. **Automated Course & Assessment Generation:**
   * Staff select indexed documents and prompt the engine (e.g., "Generate a 4-module intermediate course for DevOps Engineers").
   * The backend generates structured modules, lessons, exercises, and quizzes.
   * Generated quiz questions undergo automated sanity validation (detecting duplicates, vague answer choices like "all of the above", or answers missing from the options). Courses land in `review` status until published by an authorized human.
3. **Learner Portal (`/learn`):**
   * Learners log in to a dedicated portal. They only see courses they are actively enrolled in and cannot probe other learners' records (cross-tenant and cross-learner access returns `404 Not Found`, not `403 Forbidden`).
   * Learners read lessons, track module progress, and take assessments.
   * The **Learner AI Assistant** allows students to chat with the course material, grounding responses with citations and providing tools to query personal progress or open support tickets.
4. **Assessment Evaluation & Reviewer Queue:**
   * Objective questions are graded immediately by the server.
   * Free-text essays are evaluated by the AI against a grading rubric.
   * If any question's AI confidence is $< 0.75$, the submission is flagged `needs_review` and queued for human instructor sign-off.
5. **Event Automation via n8n:**
   * Domain events (e.g., `learner.created`, `ticket.created`, `evaluation.needs_review`, `schedule.daily`) trigger HMAC-signed webhook dispatches to n8n workflows (or fallback Python runners) to automate welcome emails, inactivity nudges, and ticket triage.

---

## 4. Technology Stack

| Layer | Technologies / Libraries | Architectural Role |
|---|---|---|
| **Frontend Web App** | Next.js 15.1 (App Router), React 19, TypeScript | Server and client components, role-based layout splitting (`/dashboard` vs `/learn`). |
| **State & Data Fetching**| TanStack Query v5 (React Query), Native `fetch` SSE parser | Cache invalidation, optimistic updates, live SSE streaming without WebSocket overhead. |
| **UI & Styling** | Tailwind CSS v3.4, Lucide React, Recharts, Sonner | Modern dark/light UI dashboard, charts for token spend and latency, toast notifications. |
| **Backend API** | FastAPI 0.115, Uvicorn, Python 3.11/3.12 | Async REST API, pure ASGI middleware pipeline, OpenAPI auto-generation. |
| **Async Worker** | ARQ 0.26 (Redis-based), Asyncio | Distributed background task execution, scheduled cron sweeps, job status tracking. |
| **Primary Relational DB** | PostgreSQL 16 (production) / SQLite (tests), SQLAlchemy 2.0 (Async), Alembic | Tenant-scoped relational models, JSONB support, composite indexing, migration tracking. |
| **Vector Database** | ChromaDB 0.5.20 (HTTP / In-Memory adapter) | Dedicated vector collection per organization (`org_<uuid.hex>`), cosine similarity search. |
| **Cache & Message Broker**| Redis 7 (Alpine) | Fixed-window rate limiting, Pub/Sub channels for SSE live streaming, ARQ job queues. |
| **Workflow Automation** | n8n 1.70 (Workflow Engine) + Built-in Python Runners | External visual orchestrator for emails and notifications with local deterministic fallback. |
| **AI / LLM Integration** | OpenAI API (`gpt-4o-mini`, `text-embedding-3-small`), Azure OpenAI, Ollama, HuggingFace TGI, Mock Provider | Swappable AI providers via environment variables, token budgeting with `tiktoken`, custom AI Gateway. |
| **Security & Auth** | PyJWT (HS256), bcrypt (with SHA-256 pre-hashing), HMAC-SHA256 | Single-use rotating refresh tokens, API key hashing, constant-time signature verification. |
| **Document Processing** | `pypdf` 5.1, `python-docx` 1.1, CSV/Markdown parsers | Multi-format text extraction, magic-bytes file identification. |
| **Observability & Logs** | Structlog 24.4 | Structured JSON logging, automated secret redaction, request ID correlation. |
| **Container & Orchestration**| Docker Compose, Kubernetes (23 custom manifests + Kustomize) | Multi-container local development stack and zero-drift Kubernetes infrastructure. |

---

## 5. High-Level Architecture

The system is designed as an asynchronous, event-driven, multi-tenant application where FastAPI acts as the synchronous gateway and ARQ/Redis acts as the asynchronous execution engine.

```
                                      ┌─────────────────────────────────────────┐
                                      │           Client Web Browser            │
                                      │   (Next.js 15 App Router + SSE Client)  │
                                      └────────────────────┬────────────────────┘
                                                           │ HTTPS / REST / SSE
                                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                             FastAPI Backend Service                                             │
│                                                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                                            ASGI Middleware Stack                                        │   │
│   │   [RequestContext (UUID)] ──► [RateLimiter (Redis)] ──► [UnitOfWork (Pre-commit DB Session Lifecycle)]   │   │
│   └────────────────────────────────────────────────────┬────────────────────────────────────────────────────┘   │
│                                                        │                                                        │
│                                                        ▼                                                        │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │                                       Authentication & Authorization                                    │   │
│   │   JWT Bearer / API Key (lf_...) ──► Role & Permission Resolution ──► Scoped Learner / Tenant Context     │   │
│   └────────────────────────────────────────────────────┬────────────────────────────────────────────────────┘   │
│                                                        │                                                        │
│                  ┌─────────────────────────────────────┴─────────────────────────────────────┐                  │
│                  ▼                                                                           ▼                  │
│   ┌───────────────────────────────┐                                           ┌─────────────────────────────┐   │
│   │       TenantRepo (DAL)        │                                           │         AI Gateway          │   │
│   │  Enforces organization_id on  │                                           │  Retries, Fallback Provider,│   │
│   │  every SELECT / INSERT / WRITE│                                           │  JSON Schema Repair, Cost & │   │
│   └──────────────┬────────────────┘                                           │  Token Tracking Observability│  │
│                  │                                                            └──────────────┬──────────────┘   │
└──────────────────┼───────────────────────────────────────────────────────────────────────────┼──────────────────┘
                   │                                                                           │
         ┌─────────┴─────────┐                                                       ┌─────────┴─────────┐
         ▼                   ▼                                                       ▼                   ▼
┌─────────────────┐ ┌─────────────────┐                                     ┌─────────────────┐ ┌─────────────────┐
│ PostgreSQL 16   │ │  Redis 7 (Broker│                                     │  ChromaDB       │ │  LLM Providers  │
│ Tenant-Scoped   │ │  & Rate Limiter)│                                     │  (Collection    │ │ (OpenAI, Azure, │
│ Relational Data │ │                 │                                     │  per Org)       │ │  Ollama, Mock)  │
└─────────────────┘ └────────┬────────┘                                     └─────────────────┘ └─────────────────┘
                             │
                             ▼ Enqueue Task
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                                ARQ Async Worker                                                 │
│                                                                                                                 │
│   ┌───────────────────────────┐     ┌───────────────────────────┐     ┌─────────────────────────────────────┐   │
│   │ 10-Stage Document Pipeline│     │ Course / Quiz Generation  │     │ Assessment Evaluator (Human Gate)   │   │
│   └───────────────────────────┘     └───────────────────────────┘     └─────────────────────────────────────┘   │
│   ┌─────────────────────────────────────────────────────────────┐     ┌─────────────────────────────────────┐   │
│   │ Workflow Dispatcher: Outbound HMAC Webhook to n8n           │ ──► │ n8n Automation (Callbacks / Email)  │   │
│   │ (Falls back to Built-in Python Runners if n8n is offline)   │     │                                     │   │
│   └─────────────────────────────────────────────────────────────┘     └─────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Architectural Layer Breakdown
1. **Presentation Layer (Next.js 15):** Uses React Server Components (RSC) and Client Components with TanStack Query. It splits interfaces into an administrative Staff Dashboard (`/dashboard`) and a Learner Portal (`/learn`).
2. **Gateway & Middleware Layer:** Pure ASGI middleware intercepts requests to bind correlation IDs, enforce rate limits via Redis atomic counters, and manage database transactions via `UnitOfWorkMiddleware` so commits complete before headers are sent.
3. **Data Access Layer (`TenantRepo`):** An abstraction that wraps SQLAlchemy 2.0 `AsyncSession`. It dynamically injects `where(Model.organization_id == current_org)` into queries and stamps `organization_id` onto all inserts, preventing cross-tenant data leaks.
4. **AI Gateway (`ai/gateway.py`):** Acts as the centralized choke point for LLM interactions. It abstracts API differences across providers (OpenAI, Azure, Ollama, Mock), implements automated retries with exponential backoff, validates structured Pydantic outputs with auto-repair prompts, and writes detailed execution logs to `llm_calls`.
5. **Asynchronous Background Processing (ARQ):** Manages compute-heavy operations (file parsing, embedding generation, course synthesis, workflow executions) outside the synchronous HTTP request-response cycle.

---

## 6. Architecture Diagram

```mermaid
flowchart TB
    subgraph Client["Frontend Client (Next.js 15)"]
        UI_Admin["Staff Dashboard (/dashboard)"]
        UI_Learner["Learner Portal (/learn)"]
        SSE_Consumer["SSE Stream Reader (Native Fetch)"]
    end

    subgraph Middleware["FastAPI ASGI Middleware"]
        REQ_ID["RequestContextMiddleware<br/>(Binds UUID to Logs)"]
        RL["RateLimitMiddleware<br/>(Redis Fixed Window)"]
        UOW["UnitOfWorkMiddleware<br/>(Pre-response DB Commit)"]
    end

    subgraph Auth["Security & Auth Layer"]
        AUTH_DEP["deps.get_principal"]
        JWT_VAL["JWT / API Key Hash Resolver"]
        RBAC["ROLE_PERMISSIONS Guard"]
    end

    subgraph Core["Core Business Services"]
        REPO["TenantRepo (DAL)<br/>Mandatory Tenant Filter"]
        GATEWAY["AIGateway<br/>Retries · Fallback · Costing"]
        RAG_ENGINE["RAG Engine<br/>Dense + Keyword RRF"]
        EVAL_SVC["Evaluation Service<br/>Rules + LLM Clamping"]
        WORKFLOW_ENG["Workflow Engine<br/>n8n Client + Builtin Fallback"]
    end

    subgraph Storage["Datastores & Infra"]
        PG[("PostgreSQL 16<br/>Tenant Relational DB")]
        REDIS[("Redis 7<br/>Broker · PubSub · Rate Limits")]
        CHROMA[("ChromaDB<br/>org_{hex} Collections")]
        UPLOADS[("Upload Volume<br/>/data/uploads")]
    end

    subgraph Workers["Background Execution"]
        ARQ["ARQ Worker Pool (Concurrency: 5)"]
        CRON["Worker Cron<br/>(Inactivity Sweep & n8n Sync)"]
    end

    subgraph External["External Services"]
        N8N_SVC["n8n Workflow Engine"]
        LLM_SVC["LLM Providers (OpenAI / Azure / Ollama)"]
    end

    UI_Admin -->|REST / Multipart| REQ_ID
    UI_Learner -->|REST| REQ_ID
    SSE_Consumer -->|SSE GET| REQ_ID

    REQ_ID --> RL --> UOW --> AUTH_DEP --> JWT_VAL --> RBAC

    RBAC --> REPO
    RBAC --> GATEWAY
    RBAC --> RAG_ENGINE
    RBAC --> EVAL_SVC
    RBAC --> WORKFLOW_ENG

    REPO --> PG
    GATEWAY --> LLM_SVC
    GATEWAY --> PG
    RAG_ENGINE --> CHROMA
    RAG_ENGINE --> REPO
    RAG_ENGINE --> GATEWAY

    WORKFLOW_ENG -->|Enqueue Job| REDIS
    WORKFLOW_ENG -->|Signed HMAC Webhook| N8N_SVC
    N8N_SVC -->|Signed HMAC Callback| Core

    REDIS --> ARQ
    CRON --> ARQ
    ARQ --> REPO
    ARQ --> GATEWAY
    ARQ --> CHROMA
    ARQ --> UPLOADS
    ARQ -->|Progress PubSub| REDIS
    REDIS -->|Subscribe| SSE_Consumer
```

---

## 7. Why the Architecture Looks Like This

### 1. Dedicated Chroma Collection per Tenant vs. Metadata Filtering
* **Decision:** Assign every organization its own ChromaDB collection named `org_<uuid.hex>`.
* **Likely Reason:** Avoiding cross-tenant vector contamination.
* **Benefit:** Metadata filters (`where={"org_id": "..."}`) fail open—if a query omits the filter due to a bug, it retrieves global documents. Collection isolation fails closed—an incorrect collection name returns nothing.
* **Tradeoff:** Creating thousands of collections in ChromaDB increases index metadata overhead.
* **Alternatives:** Single shared collection with strict metadata filtering or multi-tenant managed solutions (Pinecone namespaces).
* **When to Change:** If tenant count exceeds $10,000$ active organizations, migrate to a vector engine optimized for massive namespace cardinality or multi-tenant partitioning (e.g., pgvector with Row-Level Security).

### 2. Pure ASGI Middleware vs. Starlette `BaseHTTPMiddleware`
* **Decision:** Implement `apps/api/learnflow/middleware.py` directly using the ASGI specification (`__call__(scope, receive, send)`).
* **Likely Reason:** Starlette's `BaseHTTPMiddleware` wraps request processing in a background streaming task. In FastAPI, `yield` dependencies (like DB sessions) execute teardown *after* the response body has left the server. If a client registers and immediately attempts to log in, a delayed database commit causes a race condition where the user is not found.
* **Benefit:** `UnitOfWorkMiddleware` intercepts `http.response.start` and commits the SQLAlchemy transaction *before* the first response byte reaches the client, ensuring read-after-write consistency.
* **Tradeoff:** Writing raw ASGI middleware requires manual byte handling and header manipulation.
* **Alternatives:** Explicit `await session.commit()` calls in every router endpoint.
* **When to Change:** Never; pure ASGI middleware is the gold standard for reliable transactional pipelines in Python.

### 3. ARQ (Redis) vs. Celery
* **Decision:** Use [ARQ](https://github.com/samuelcolvin/arq) as the async job worker.
* **Likely Reason:** The entire backend is built on modern `asyncio`. Celery's architecture is historically synchronous/multiprocessing-based and introduces heavyweight AMQP/Redis protocol serialization layers.
* **Benefit:** Direct sharing of async SQLAlchemy sessions, HTTPX clients, and coroutines between the FastAPI API and ARQ worker processes.
* **Tradeoff:** ARQ lacks advanced enterprise features like complex task canvas chaining, rate-limited task groups, and built-in task revocation dashboards.
* **Alternatives:** Celery, Temporal, TaskIQ, BullMQ (Node.js).
* **When to Change:** When workflow orchestration requires multi-day durable execution states, distributed saga compensation, or complex human-in-the-loop task DAGs (where Temporal would excel).

### 4. Reciprocal Rank Fusion (RRF) Hybrid Search vs. Pure Dense Retrieval
* **Decision:** Combine Chroma dense vector search with a PostgreSQL `ILIKE` keyword pass using Reciprocal Rank Fusion ($k=60$).
* **Likely Reason:** Corporate training documents frequently contain unique alphanumeric tokens (e.g., error codes `ERR_4019`, policy codes `HR-POL-2026`, SKU numbers).
* **Benefit:** Dense semantic embeddings often fail on exact keyword lookups because out-of-vocabulary tokens have diffuse vector representations. RRF merges keyword precision with semantic breadth.
* **Tradeoff:** Extra database query overhead on Postgres for every search; requires query term parsing and stopword stripping.
* **Alternatives:** Full-text search with PostgreSQL `tsvector`/`tsquery`, Elasticsearch, BM25 rerankers, Cohere Rerank API.
* **When to Change:** When document corpus size exceeds millions of chunks, replace the `ILIKE` pass with Postgres Full-Text Search (GIN indexes) or an integrated BM25/Dense engine like Qdrant or Vespa.

### 5. Deterministic Mock Provider vs. Mandatory Live LLM API Keys
* **Decision:** Implement `apps/api/learnflow/ai/mock_provider.py` providing schema-valid deterministic JSON and hash-based embeddings.
* **Likely Reason:** Allowing local testing, CI/CD pipelines, and interview reviews to execute without requiring paid third-party API keys.
* **Benefit:** Zero external dependencies for tests; tests run on SQLite with in-memory vectors in seconds.
* **Tradeoff:** Mock responses contain synthetic structural text rather than natural semantic completions.
* **Alternatives:** Relying solely on OpenAI or requiring local Ollama setup.
* **When to Change:** Production runtime always sets `LLM_PROVIDER=openai` or `azure_openai`.

---

## 8. Repository / Folder Map

```
learnflow-ai/
├── apps/
│   ├── api/                                 # FastAPI Backend Service
│   │   ├── alembic/                         # DB Migration scripts (0001_initial_schema)
│   │   ├── learnflow/                       # Application Root Python Package
│   │   │   ├── ai/                          # LLM Gateway & Intelligence Layer
│   │   │   │   ├── base.py                  # LLMClient & Embedder abstract protocols
│   │   │   │   ├── gateway.py               # Central AI Gateway (Retries, fallback, metrics)
│   │   │   │   ├── mock_provider.py         # Offline deterministic LLM & embedder
│   │   │   │   ├── openai_provider.py       # OpenAI & Azure OpenAI API client
│   │   │   │   ├── ollama_provider.py       # Local Ollama client
│   │   │   │   ├── pricing.py               # Token cost calculation engine
│   │   │   │   ├── prompts.py               # Versioned prompt definitions
│   │   │   │   ├── registry.py              # Provider factory / dependency resolution
│   │   │   │   ├── structured.py            # Pydantic models for structured output
│   │   │   │   └── tools.py                 # Tool definitions for Assistant function calling
│   │   │   ├── automation/                  # Workflow Execution & Automation Engine
│   │   │   │   ├── email.py                 # Mock / SMTP email adapters
│   │   │   │   ├── engine.py                # Workflow runner, step recorder & callback logic
│   │   │   │   ├── events.py                # Internal domain event dispatcher
│   │   │   │   ├── n8n_client.py            # HTTP client for triggering n8n webhooks
│   │   │   │   └── runners.py               # Built-in Python workflow runners
│   │   │   ├── models/                      # SQLAlchemy 2.0 Database Models
│   │   │   │   ├── ai.py                    # LLMCall, UsageRecord, Conversation, Message
│   │   │   │   ├── automation.py            # Workflow, WorkflowExecution, WorkflowStep
│   │   │   │   ├── base.py                  # Base entity classes, UUIDs, UTC helpers
│   │   │   │   ├── knowledge.py             # KnowledgeBase, Document, DocumentChunk, PipelineRun
│   │   │   │   ├── learning.py              # Course, Module, Lesson, Assessment, Submission, Evaluation
│   │   │   │   ├── platform.py              # APIKey, AuditLog, Job
│   │   │   │   └── tenancy.py               # User, Organization, Member, Learner, Role
│   │   │   ├── rag/                         # Retrieval-Augmented Generation Subsystem
│   │   │   │   ├── chunking.py              # Sentence-aware chunker with token budgets
│   │   │   │   ├── extract.py               # Multi-format document parser & magic-byte validator
│   │   │   │   ├── pipeline.py              # 10-stage ingestion pipeline implementation
│   │   │   │   ├── retrieval.py             # Dense + Keyword RRF retrieval & refusal logic
│   │   │   │   └── vectorstore.py           # ChromaDB client & In-Memory test store
│   │   │   ├── routers/                     # REST API Endpoints (/api/v1)
│   │   │   │   ├── analytics.py             # Usage, spend, and learning analytics
│   │   │   │   ├── assessments.py           # Quiz generation, submission, review
│   │   │   │   ├── assistant.py             # Learner tool assistant & content transform
│   │   │   │   ├── auth.py                  # Register, login, refresh, logout
│   │   │   │   ├── courses.py               # Course CRUD, generation, publishing
│   │   │   │   ├── knowledge.py             # Knowledge base & document management
│   │   │   │   ├── learners.py              # Learner records & portal self-scoping
│   │   │   │   ├── organizations.py         # Org management & member invites
│   │   │   │   ├── platform.py              # API keys, jobs status, audit logs
│   │   │   │   ├── rag.py                   # Direct RAG query endpoint
│   │   │   │   ├── tickets.py               # Support ticket management
│   │   │   │   ├── webhooks.py              # n8n signed callbacks & external events
│   │   │   │   └── workflows.py             # Workflow configuration & manual runs
│   │   │   ├── schemas/                     # Pydantic Request & Response DTOs
│   │   │   ├── services/                    # Business Logic Layer
│   │   │   │   ├── audit.py                 # Audit logging helper
│   │   │   │   ├── evaluation.py            # Assessment grading logic
│   │   │   │   ├── generation.py            # Course & assessment generation logic
│   │   │   │   ├── jobs.py                  # Job lifecycle and ARQ queue dispatch
│   │   │   │   └── repository.py            # TenantRepo data access abstraction
│   │   │   ├── worker/                      # ARQ Background Task Worker
│   │   │   │   ├── main.py                  # ARQ worker settings and cron definitions
│   │   │   │   ├── standalone.py            # Fallback DB poller when Redis is disabled
│   │   │   │   └── tasks.py                 # Task handlers for background jobs
│   │   │   ├── config.py                    # Pydantic BaseSettings environment config
│   │   │   ├── db.py                        # Async database engine & sessionmaker setup
│   │   │   ├── deps.py                      # FastAPI Request dependencies (Principal, Auth)
│   │   │   ├── errors.py                    # Custom domain exception hierarchy & handlers
│   │   │   ├── logging.py                   # Structlog configuration with secret redaction
│   │   │   ├── main.py                      # FastAPI application instantiation & lifespan
│   │   │   ├── middleware.py                # Pure ASGI middleware (Logging, RateLimit, UoW)
│   │   │   ├── redis_client.py              # Async Redis connection pool manager
│   │   │   ├── security.py                  # Cryptographic helpers (JWT, bcrypt, HMAC)
│   │   │   └── seed.py                      # Database seeder (realistic 30-day demo dataset)
│   │   ├── tests/                           # 59 Pytest backend test suites
│   │   ├── Dockerfile                       # Multi-stage container build (base & worker)
│   │   └── pyproject.toml                   # Python dependencies and tool configs
│   │
│   └── web/                                 # Next.js 15 Frontend Web Application
│       ├── app/                             # Next.js App Router Structure
│       │   ├── (app)/                       # Shared application layouts
│       │   ├── api/health/                  # Next.js health check endpoint
│       │   ├── dashboard/                   # Staff/Admin Dashboard routes (20 sub-features)
│       │   ├── learn/                       # Learner Portal routes (courses, lessons, quiz)
│       │   ├── login/ & register/           # Authentication pages
│       │   ├── layout.tsx                   # Root HTML/Body layout
│       │   └── page.tsx                     # Landing / route dispatcher page
│       ├── components/                      # Shared React UI Components
│       │   ├── shell.tsx                    # Top navigation & sidebar application shell
│       │   ├── providers.tsx                # TanStack Query & Theme providers
│       │   ├── pagination.tsx               # Reusable table pagination component
│       │   └── ui/                          # Button, Card, Badge, Modal, Input primitives
│       ├── lib/                             # Frontend Utilities & API Client
│       │   ├── api.ts                       # Typed fetch wrapper with single-flight refresh & SSE
│       │   ├── format.ts                    # Date, currency, duration formatters
│       │   └── types.ts                     # TypeScript interfaces matching backend DTOs
│       ├── tests/                           # Vitest frontend unit tests
│       ├── e2e/                             # Playwright end-to-end browser test suite
│       ├── package.json                     # Frontend dependencies & npm scripts
│       └── tailwind.config.ts               # Tailwind design system configuration
│
├── automation/                              # Workflow Automation Assets
│   └── n8n-workflows/                       # 5 Production-ready JSON workflow definitions
│
├── infrastructure/                          # Cloud & Deployment Configuration
│   ├── docker/                              # Specialized Docker build files
│   └── kubernetes/                          # 23 Manifests (Deployments, StatefulSets, Ingress)
│
├── scripts/                                 # Code & Manifest Generators
│   ├── build_k8s_manifests.py               # Generates infrastructure/kubernetes/*.yaml
│   └── build_n8n_workflows.py               # Generates automation/n8n-workflows/*.json
│
├── docs/                                    # Architectural & Operational Documentation
├── docker-compose.yml                       # Full-stack local development compose definition
└── .env.example                             # Environment variable template
```

---

## 9. Important Entry Points

### 1. Web Application Entry Point (`apps/web`)
* **Startup:** `npm run dev` or `next start` inside `apps/web`.
* **Initialization Order:**
  1. Next.js loads `app/layout.tsx`, mounting `Providers` (TanStack Query client and theme providers).
  2. `app/page.tsx` evaluates client authentication state via `localStorage`.
  3. If authenticated, the user is routed by role: learners go to `/learn`, while staff roles (`org_admin`, `instructor`, `reviewer`, `support_agent`) go to `/dashboard`. Unauthenticated visitors redirect to `/login`.

### 2. Backend API Entry Point (`apps/api`)
* **Startup:** `uvicorn learnflow.main:app --host 0.0.0.0 --port 8000`.
* **Initialization Order:**
  1. `learnflow/logging.py` initializes Structlog with secret redaction and JSON output formatting.
  2. `learnflow/main.py` registers the `lifespan` context manager.
  3. Pure ASGI middlewares are registered in reverse execution order: `RequestContextMiddleware` $\rightarrow$ `RateLimitMiddleware` $\rightarrow$ `UnitOfWorkMiddleware` $\rightarrow$ `CORSMiddleware`.
  4. Global exception handlers are attached via `install_error_handlers(app)`.
  5. The versioned router `routers/__init__.py` mounts all domain routes under `/api/v1`.
  6. On startup, database connection pools and Redis clients initialize. On shutdown, pools are cleanly drained.

### 3. Background Worker Entry Point (`apps/api`)
* **Startup:** `arq learnflow.worker.main.WorkerSettings`.
* **Initialization Order:**
  1. ARQ connects to Redis using `RedisSettings`.
  2. Registers task functions: `ingest_document`, `generate_course`, `regenerate_module`, `generate_assessment`, `evaluate_submission`, `run_workflow`, `transform_content`.
  3. Registers cron schedules: `sync_n8n_executions` (every 5 minutes) and `scan_inactive_learners` (daily at 07:00 UTC).
  4. Spawns an async worker pool with a maximum concurrency of 5 jobs.

---

## 10. End-to-End Application Flows

### Flow 1: Document Upload to Citation-Backed Ingestion

```mermaid
sequenceDiagram
    autonumber
    actor Staff as Instructor / Admin
    participant Web as Next.js Web App
    participant API as FastAPI Router
    participant UOW as UnitOfWork / DB
    participant Redis as Redis / Broker
    participant Worker as ARQ Worker
    participant Chroma as ChromaDB

    Staff->>Web: Select PDF & click Upload
    Web->>API: POST /api/v1/documents (Multipart)
    API->>API: Validate Magic Bytes (%PDF-), Checksum, Extension
    API->>API: Save raw file to /data/uploads/{uuid}_{filename}
    API->>UOW: INSERT INTO documents (status="pending")
    API->>Redis: Enqueue ingest_document(job_id, document_id)
    API-->>Web: 202 Accepted (document_id, job_id)
    Web->>API: GET /api/v1/documents/{id}/stream (SSE)

    Worker->>Redis: Claim ingest_document job
    loop 10-Stage Pipeline
        Worker->>UOW: INSERT INTO pipeline_runs (stage, status="running")
        Worker->>Redis: PUBLISH learnflow:document:{id} (Progress Event)
        Redis-->>Web: SSE Push: {"stage": "...", "progress": 0.X}
        Worker->>Worker: Execute stage logic (Extract, Clean, Chunk, Embed)
        Worker->>UOW: UPDATE pipeline_runs (status="succeeded", duration_ms)
    end
    Worker->>Chroma: Delete old vectors for doc, Upsert new VectorRecords
    Worker->>UOW: INSERT INTO document_chunks, UPDATE documents (status="indexed")
    Worker->>Redis: PUBLISH learnflow:document:{id} (Completed)
    Redis-->>Web: SSE Push: {"stage": "completed", "status": "succeeded"}
```

1. **Upload & Magic-Byte Validation:** The user posts a file to `/api/v1/documents`. The endpoint inspects the file's binary header (magic bytes) to ensure a file named `manual.pdf` actually begins with `%PDF-` and is not an encrypted or disguised payload.
2. **Storage & Database Record:** The file is written to `/data/uploads/{uuid}_{filename}`. A `documents` row is created with `status="pending"`.
3. **Queue Dispatch:** A job is enqueued in ARQ with a deterministic idempotency key. The API returns `202 Accepted`.
4. **SSE Subscription:** The browser establishes an SSE connection to `/api/v1/documents/{id}/stream`.
5. **Worker Execution (10 Stages):**
   * `upload`: Reads stored bytes from the filesystem.
   * `validation`: Re-verifies SHA-256 checksum against the database record.
   * `extraction`: Extracts text using `pypdf`, `python-docx`, or text parsers; captures page numbers.
   * `cleaning`: Normalizes Unicode (NFKC), reconnects hyphen-broken words, and strips non-printable control characters.
   * `metadata`: Calculates word counts, guesses document titles, and infers language via stopword analysis.
   * `chunking`: Packs full sentences into token budgets ($700$ tokens) with carried-forward overlap ($100$ tokens).
   * `embedding`: Batches chunks in groups of 64 through `gateway.embed()` to generate 1536-dimensional embeddings.
   * `indexing`: Clears existing vectors for this document and upserts new embeddings into ChromaDB collection `org_<uuid.hex>`, while simultaneously writing `document_chunks` rows to PostgreSQL.
   * `quality`: Checks for empty or oversized chunks (fails if $>50\%$ of chunks are near-empty).
   * `completed`: Marks the document as `indexed` and updates knowledge base counters.
6. **Live SSE Streaming:** Every stage transition publishes a JSON payload to Redis channel `learnflow:document:{document_id}`, which forwards it to the active SSE HTTP connection.

---

### Flow 2: Grounded RAG Query Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Learner as Learner / User
    participant API as FastAPI (/rag/query)
    participant Gateway as AI Gateway
    participant Chroma as ChromaDB (org_{hex})
    participant DB as PostgreSQL
    participant LLM as External LLM Provider

    Learner->>API: POST /api/v1/rag/query {"query": "What is policy HR-20?"}
    API->>Gateway: embed(["What is policy HR-20?"])
    Gateway->>LLM: POST /embeddings (or mock)
    Gateway->>DB: INSERT INTO usage_records (embedding cost)
    Gateway-->>API: Dense vector [0.012, -0.045, ...]

    par Dense & Keyword Retrieval
        API->>Chroma: query(collection, vector, top_k=12)
        Chroma-->>API: Dense Matches (scores, vector_ids)
    and
        API->>DB: SELECT chunk_id FROM document_chunks WHERE content ILIKE '%hr-20%'
        DB-->>API: Keyword Matches (chunk_ids)
    end

    API->>API: Reciprocal Rank Fusion (RRF k=60)
    API->>API: Filter scores < RETRIEVAL_MIN_SCORE (0.25)

    alt No Chunks Pass Threshold
        API-->>Learner: Refusal: "I don't have enough information..." (0 tokens used)
    else Grounded Context Found
        API->>DB: Fetch chunk text, document name, page, section
        API->>Gateway: complete("rag_answer", prompt_v3, context)
        Gateway->>LLM: POST /chat/completions (temperature=0.1)
        LLM-->>Gateway: Completion with [1], [2] citations
        Gateway->>DB: INSERT INTO llm_calls + usage_records
        API-->>Learner: Answer text + Citations list + Latency & Cost metadata
    end
```

1. **Embedding Generation:** The incoming query is embedded via `AIGateway.embed()`.
2. **Hybrid Search Execution:**
   * **Dense Search:** Queries ChromaDB collection `org_<uuid.hex>` for the top $2 \times k$ vectors.
   * **Keyword Search:** Extracts non-stopword tokens and runs an `ILIKE` query across PostgreSQL `document_chunks`.
3. **Reciprocal Rank Fusion (RRF):** Ranks combined results using:
   $$\text{RRF Score} = \sum_{m \in M} \frac{1}{60 + \text{rank}_m}$$
4. **Hard Grounding Gate:** If no chunks achieve an RRF score $\ge 0.25$, the pipeline halts. The API immediately returns a standardized refusal message without calling the generation LLM, saving inference costs.
5. **Prompt Assembly & LLM Call:** Context passages are numbered `[1] (doc_name, page N, section S): text`. The `rag_answer` prompt instructs the model to answer strictly using provided passages with inline citations.
6. **Telemetry Logging:** `llm_calls` records input/output tokens, latency in milliseconds, model name, prompt version, and estimated cost.

---

### Flow 3: Assessment Submission, AI Evaluation, and Human Review Gate

```mermaid
sequenceDiagram
    autonumber
    actor Learner as Learner
    participant API as FastAPI (/assessments/submit)
    participant EvalSvc as Evaluation Service
    participant Gateway as AI Gateway
    participant DB as PostgreSQL
    actor Reviewer as Instructor / Reviewer

    Learner->>API: POST /api/v1/assessments/{id}/submit {answers: [...]}
    API->>DB: INSERT INTO submissions (status="submitted")
    API->>EvalSvc: evaluate_submission(submission)

    loop For Each Question
        alt Objective (Multiple Choice / True-False)
            EvalSvc->>EvalSvc: Exact match normalization in code (Score: Max, Confidence: 1.0)
        else Subjective (Short Answer / Essay)
            alt Answer is Empty
                EvalSvc->>EvalSvc: Score: 0.0, Confidence: 1.0
            else Answer Present
                EvalSvc->>Gateway: complete("assessment_evaluate", rubric, reference, answer)
                Gateway-->>EvalSvc: Parsed JSON {score, confidence, rationale, strengths, missing}
                EvalSvc->>EvalSvc: Clamp score to question.points
            end
        end
    end

    EvalSvc->>EvalSvc: Calculate Overall Confidence = min(question_confidences)

    alt Overall Confidence >= 0.75
        EvalSvc->>DB: UPDATE evaluations (status="ai_completed", requires_human_review=False)
        API-->>Learner: 200 OK (Score & Breakdown immediately visible)
    else Overall Confidence < 0.75
        EvalSvc->>DB: UPDATE evaluations (status="needs_review", requires_human_review=True)
        EvalSvc->>DB: EMIT domain event: "evaluation.needs_review"
        API-->>Learner: 200 OK (Status: "needs_review", score marked provisional)
        
        Reviewer->>API: GET /api/v1/assessments/reviews/queue
        API-->>Reviewer: List of submissions flagged for review
        Reviewer->>API: POST /api/v1/assessments/evaluations/{id}/review {approve: true, score_override: 8.5}
        API->>DB: UPDATE evaluations (status="approved", reviewed_by=user.id)
    end
```

---

### Flow 4: n8n Workflow Trigger & Callback Loop

```mermaid
sequenceDiagram
    autonumber
    participant App as LearnFlow Backend
    participant DB as PostgreSQL
    participant Worker as ARQ Worker
    participant n8n as n8n Instance
    participant Callback as /api/v1/webhooks/n8n/callback

    App->>DB: EMIT event "learner.created"
    App->>DB: INSERT INTO workflow_executions (status="queued", idempotency_key=UUID)
    App->>Worker: Enqueue run_workflow(execution_id)

    Worker->>DB: UPDATE workflow_executions (status="running")
    Worker->>Worker: Sign payload: HMAC-SHA256(secret, "t.body")
    Worker->>n8n: POST /webhook/learnflow/learner-onboarding (Header: X-LearnFlow-Signature)

    alt n8n Available
        n8n-->>Worker: 200 OK { executionId: "4242" }
        Worker->>DB: UPDATE workflow_executions (n8n_execution_id="4242", handoff="n8n")
        n8n->>n8n: Execute Code & HTTP Nodes (Verify Sig, Email, DB calls)
        n8n->>Callback: POST /webhooks/n8n/callback (X-LearnFlow-Signature, execution_id, steps)
        Callback->>Callback: Verify HMAC signature & 300s freshness
        Callback->>DB: INSERT INTO webhook_receipts (signature=mac) [Replay Check]
        Callback->>DB: UPDATE workflow_executions (status="succeeded", steps, output)
    else n8n Down or Unconfigured
        Worker->>Worker: Fall back to Python runner (runners.learner_onboarding)
        Worker->>DB: Execute steps locally & save step rows
        Worker->>DB: UPDATE workflow_executions (status="succeeded", source="builtin")
    end
```

---

## 11. Data Flow

### Data Entities & Transformation Pipeline

```
[Raw Files / Uploads] 
       │ (extract.py)
       ▼
[Text Segments + Page/Section Metadata] 
       │ (clean.py)
       ▼
[Cleaned Unicode Text] 
       │ (chunking.py)
       ▼
[Document Chunks (700 tokens, 100 overlap)] 
       │ (gateway.embed)
       ▼
[1536-dim Vectors] ──► Upsert to ChromaDB (`org_{hex}`)
       │
       ▼
[PostgreSQL `document_chunks`] ──► Relational Search Index
```

### Where State Lives

| State Scope | Storage Mechanism | Persistence / Lifetime | Description |
|---|---|---|---|
| **Client Auth & Org Context** | Browser `localStorage` | Persistent across sessions | Access JWT, single-use refresh token, active `organization_id`. |
| **Server Transactional State**| Request `state.db_session` | Per-request lifecycle | Handled by `UnitOfWorkMiddleware`; auto-commits before response leaves. |
| **Rate Limit Counters** | Redis Key `rl:{hash}:{minute}`| 90-second TTL | Fixed-window counter incremented atomically via `INCR`. |
| **Job Queue & Cron State** | Redis (ARQ Key Stores) | Ephemeral / Execution-bound| Serialized task arguments, job status tracking, cron metadata. |
| **Relational Domain Entities**| PostgreSQL 16 Tables | Permanent ACID storage | Organizations, users, courses, submissions, evaluation records. |
| **Vector Embeddings** | ChromaDB Persistent Volume | Permanent / Deduplicated | Chunk embeddings and metadata attributes. |
| **Raw Training Files** | Shared Storage (`/data/uploads`)| Permanent | Raw uploaded PDF, DOCX, and CSV source binaries. |
| **Workflow Step Audit** | PostgreSQL `workflow_steps` | Permanent | Granular breakdown of every workflow execution step. |

---

## 12. Database Architecture

### Technology & Conventions
* **Engine:** PostgreSQL 16 using async connections (`asyncpg` driver).
* **ORM:** SQLAlchemy 2.0 declarative models with `Mapped[]` type annotations.
* **Keys & Timestamps:** All primary keys are Python-generated UUIDv4 strings/UUIDs. Timestamps are explicitly timezone-aware (`DateTime(timezone=True)`).
* **Tenant Isolation:** Every tenant-scoped table has an `organization_id` foreign key with `ON DELETE CASCADE` and a composite index on `(organization_id, created_at)`.

### Core Tables & Entity Relationship Diagram

```mermaid
erDiagram
    ORGANIZATIONS ||--o{ USERS : members
    ORGANIZATIONS ||--o{ WORKSPACES : contains
    ORGANIZATIONS ||--o{ LEARNERS : contains
    ORGANIZATIONS ||--o{ KNOWLEDGE_BASES : contains
    ORGANIZATIONS ||--o{ COURSES : owns
    ORGANIZATIONS ||--o{ WORKFLOWS : defines
    ORGANIZATIONS ||--o{ API_KEYS : issues

    KNOWLEDGE_BASES ||--o{ DOCUMENTS : contains
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : split_into
    DOCUMENTS ||--o{ PIPELINE_RUNS : logs

    COURSES ||--o{ MODULES : contains
    MODULES ||--o{ LESSONS : contains
    COURSES ||--o{ ASSESSMENTS : evaluates_with
    ASSESSMENTS ||--o{ QUESTIONS : contains

    LEARNERS ||--o{ ENROLLMENTS : registers
    COURSES ||--o{ ENROLLMENTS : enrolled_in
    ASSESSMENTS ||--o{ SUBMISSIONS : receives
    LEARNERS ||--o{ SUBMISSIONS : submits
    SUBMISSIONS ||--|| EVALUATIONS : graded_by

    WORKFLOWS ||--o{ WORKFLOW_EXECUTIONS : triggers
    WORKFLOW_EXECUTIONS ||--o{ WORKFLOW_STEPS : records
    WORKFLOW_EXECUTIONS ||--o{ LLM_CALLS : attributes
```

### Table Breakdown

#### 1. Tenancy Subsystem
* `users`: Global user accounts. (`id`, `email` [UNIQUE], `password_hash`, `is_platform_admin`, `is_active`).
* `organizations`: Tenant boundaries. (`id`, `name`, `slug` [UNIQUE], `plan`, `settings`, `is_active`).
* `organization_members`: Many-to-many relationship mapping users to organizations with explicit roles. (`organization_id`, `user_id`, `role`). Unique on `(organization_id, user_id)`.
* `learners`: Student profiles. (`id`, `organization_id`, `email`, `user_id` [Nullable], `last_active_at`, `reminders_sent`, `profile`). Note: `user_id` is linked only when portal access is explicitly granted.
* `refresh_tokens`: Single-use rotating auth tokens. (`id`, `user_id`, `token_hash` [UNIQUE], `expires_at`, `revoked_at`).

#### 2. Knowledge & RAG Subsystem
* `knowledge_bases`: Grouping container for documents. (`id`, `organization_id`, `embedding_model`, `chunk_size_tokens`, `chunk_overlap_tokens`, `document_count`, `chunk_count`).
* `documents`: Stored files. (`id`, `organization_id`, `knowledge_base_id`, `filename`, `checksum` [Indexed], `status`, `stage`, `progress`, `storage_path`, `error`).
* `document_chunks`: Extracted text passages. (`id`, `organization_id`, `document_id`, `position`, `content`, `token_count`, `page_number`, `section`, `vector_id`).
  * Unique constraint on `(document_id, position)`.
  * `vector_id` format: `"{document_id}:{position}"` (joins Postgres with Chroma).
* `pipeline_runs`: Ingestion audit log. (`id`, `organization_id`, `document_id`, `stage`, `status`, `attempt`, `duration_ms`, `error`, `details`).

#### 3. Learning & Assessment Subsystem
* `courses`: Course definitions. (`id`, `organization_id`, `title`, `slug`, `status`, `source_document_ids`, `learning_objectives`).
* `modules` & `lessons`: Ordered curriculum trees with foreign keys cascading from `courses`.
* `enrollments`: Unique link between `learner_id` and `course_id` tracking `progress` ($0.0 \rightarrow 1.0$) and `completed_lesson_ids`.
* `assessments`: Quizzes. (`id`, `organization_id`, `course_id`, `title`, `passing_score`, `difficulty`, `validation_report`).
* `questions`: Quiz items. (`id`, `assessment_id`, `position`, `type`, `prompt`, `options`, `correct_answer`, `rubric`, `points`).
* `submissions`: Student answers. (`id`, `assessment_id`, `learner_id`, `answers` [JSONB], `auto_score`, `status`).
* `evaluations`: 1-to-1 grading records for submissions. (`id`, `submission_id` [UNIQUE], `status`, `score`, `confidence`, `requires_human_review`, `per_question` [JSONB], `reviewed_by`).

#### 4. AI Observability & Automation
* `llm_calls`: Granular log of every single model call. (`id`, `organization_id`, `operation`, `provider`, `model`, `prompt_name`, `prompt_version`, `input_tokens`, `output_tokens`, `latency_ms`, `estimated_cost_usd`, `success`, `retry_count`, `workflow_execution_id`).
* `usage_records`: High-level aggregated billing ledger indexed by `occurred_at`.
* `workflows` & `workflow_executions`: Automation tracking with unique `idempotency_key` per tenant.
* `webhook_receipts`: Replay protection store. (`signature` [UNIQUE], `source`, `received_at`, `payload`).
* `api_keys`: Machine credentials. (`key_hash` [UNIQUE], `prefix`, `organization_id`, `expires_at`, `revoked_at`).

---

## 13. API Architecture

### Internal API Endpoints (`/api/v1`)

| Module | Method | Route | Auth / RBAC | Description |
|---|---|---|---|---|
| **Auth** | `POST` | `/auth/register` | Public | Registers user, creates org, grants `org_admin`, returns tokens. |
| | `POST` | `/auth/login` | Public | Verifies email/password, returns access & refresh tokens. |
| | `POST` | `/auth/refresh` | Public | Redeems single-use refresh token, rotates new pair. |
| **Knowledge** | `POST` | `/documents` | `document:write` | Uploads and validates file, enqueues ingestion pipeline. |
| | `GET` | `/documents/{id}/stream` | `rag:query` | SSE endpoint streaming real-time pipeline stage progress. |
| | `POST` | `/documents/{id}/reindex`| `document:write` | Re-triggers pipeline partway (`?from_stage=chunking`). |
| **RAG** | `POST` | `/rag/query` | `rag:query` | Hybrid retrieval query with citations and refusal logic. |
| **Courses** | `POST` | `/courses/generate` | `course:write` | Background generation of structured course outline from docs. |
| | `POST` | `/courses/{id}/publish` | `course:publish`| Promotes course from `review` to `published` status. |
| **Assessments**| `POST` | `/assessments/generate`| `assessment:write`| Generates and validates quiz questions against rubrics. |
| | `POST` | `/assessments/{id}/submit`| `learner` / Staff | Submits answers; triggers auto-grading & review gate. |
| | `POST` | `/evaluations/{id}/review`| `evaluation:approve`| Human reviewer sign-off/override (User principal required). |
| **Assistant** | `POST` | `/assistant/ask` | `rag:query` | Learner assistant with multi-turn function calling tools. |
| | `POST` | `/content/transform` | `course:write` | Async conversion of docs into flashcards, FAQs, summaries. |
| **Workflows** | `POST` | `/workflows/{id}/run` | `workflow:run` | Dispatches workflow execution to n8n or Python runner. |
| **Webhooks** | `POST` | `/webhooks/n8n/callback`| HMAC Signature | n8n execution callback with step outputs and token counts. |
| | `POST` | `/webhooks/events/{slug}/{name}`| HMAC Signature| Inbound domain event emitter from external HRIS/LMS. |
| **Analytics** | `POST` | `/analytics/query` | `analytics:read`| Cost, latency, token spend, and completion rates over time. |

---

## 14. AI/ML Architecture

```
                                    ┌───────────────────────────────────────────────────────────┐
                                    │                     AIGateway                             │
                                    │  (apps/api/learnflow/ai/gateway.py)                       │
                                    └─────────────────────────────┬─────────────────────────────┘
                                                                  │
                                      ┌───────────────────────────┴───────────────────────────┐
                                      ▼                                                       ▼
                       ┌─────────────────────────────┐                         ┌─────────────────────────────┐
                       │      Primary Provider       │                         │      Fallback Provider      │
                       │    (e.g., OpenAI / Azure)   │                         │    (e.g., Local Ollama)     │
                       └──────────────┬──────────────┘                         └──────────────┬──────────────┘
                                      │                                                       │
                                      │ (Rate Limit / Timeout / 5xx)                          │
                                      └───────────────────────────►───────────────────────────┘
                                                                  │
                                                                  ▼
                                               ┌─────────────────────────────────────┐
                                               │   Structured Pydantic Validation    │
                                               └──────────────────┬──────────────────┘
                                                                  │
                                          ┌───────────────────────┴───────────────────────┐
                                          ▼ (Valid)                                       ▼ (Invalid JSON/Schema)
                             ┌─────────────────────────┐                     ┌─────────────────────────┐
                             │ Write Telemetry Record  │                     │ Trigger Repair Prompt   │
                             │ (llm_calls + cost USD)  │                     │ (Temperature = 0.0)     │
                             └─────────────────────────┘                     └────────────┬────────────┘
                                                                                          │
                                                                                          ▼
                                                                             Validate Repaired Payload
```

### 1. Centralized AI Gateway Pattern
Every LLM call in the application routes through `AIGateway.complete()` or `AIGateway.embed()`. No router or service connects directly to OpenAI or external client libraries.
* **Backoff & Retries:** Automatically catches `ProviderRateLimited` (reads `Retry-After` headers or backs off exponentially) and `ProviderUnavailable` (retries up to 3 times).
* **Provider Fallback:** If the primary provider (e.g., `openai`) exhausts retries, the gateway falls back to `llm_fallback_provider` (e.g., `ollama` or `azure_openai`).
* **Structured Output Validation with Auto-Repair:** When a Pydantic `schema` is requested, output is extracted from markdown code fences and validated via `schema.model_validate_json()`. If validation fails, the gateway sends a repair prompt containing the error back to the model with `temperature=0.0`.
* **Telemetry & Cost Accounting:** Every round-trip generates an `LLMCall` record capturing model name, prompt template name and version, prompt/completion tokens, latency in milliseconds, error status, and calculated USD cost via `pricing.py`.

### 2. Prompt Engineering & Versioning
Prompts are defined as versioned dataclasses in `apps/api/learnflow/ai/prompts.py`. The prompt name and version integer are written to database records (`courses`, `assessments`, `llm_calls`), allowing developers to trace any historical output back to the prompt version that produced it.

### 3. Function Calling & Tool Execution
The Learner Assistant (`apps/api/learnflow/ai/tools.py`) supports dynamic multi-round tool execution (up to 4 turns):
* `search_knowledge_base`: Retrieves grounded citations from ChromaDB.
* `get_my_progress`: Queries the student's enrollments and completion percentages.
* `get_course_outline`: Retrieves module and lesson structures.
* `create_support_ticket`: Escalates questions to human staff by creating a `support_tickets` row.

---

## 15. External Services

| Service | Why Used | Data Sent | Data Received | Failure Strategy |
|---|---|---|---|---|
| **OpenAI / Azure API** | Core LLM generation & embeddings | System & user prompts, JSON schemas | Completions, token metrics, vectors | Retries with backoff $\rightarrow$ switches to fallback provider $\rightarrow$ returns 502. |
| **n8n Automation** | Visual workflow orchestration | Signed JSON webhook payloads (learner data, tickets) | Execution ID, step breakdown, execution status | Built-in Python runners execute identical steps locally. |
| **SMTP Mail Server** | Transactional emails & notifications | Recipient email, subject, text/HTML body | Delivery confirmation | In dev/test, routes through `MockEmailAdapter` (in-memory log). |
| **Ollama (Optional)** | Local model inference | Prompts, embedding text | Local completions | Falls back to mock provider if unreachable. |

---

## 16. Important Dependencies

### Backend Dependencies (`apps/api/pyproject.toml`)
* `fastapi` & `uvicorn`: Async web framework and ASGI production web server.
* `pydantic` & `pydantic-settings`: Data validation, serialization, and type-safe environment loading.
* `sqlalchemy[asyncio]` & `asyncpg`: Async ORM and PostgreSQL database driver.
* `alembic`: Database schema migrations.
* `arq` & `redis`: Async distributed task queue and Redis interface.
* `chromadb`: Vector embedding storage and similarity search.
* `tiktoken`: Token estimation and chunk budget packing (`cl100k_base`).
* `pypdf` & `python-docx`: Document parsing for PDF and Word formats.
* `pyjwt` & `bcrypt`: JWT creation/validation and password hashing.
* `structlog`: Structured JSON logging with context variable propagation.
* `sse-starlette`: Server-Sent Events implementation for FastAPI.

### Frontend Dependencies (`apps/web/package.json`)
* `next` (15.1) & `react` (19.0): React framework supporting Server Components and App Router architecture.
* `@tanstack/react-query` (v5): Client-side server-state synchronization, caching, and mutation tracking.
* `tailwindcss` & `lucide-react`: Utility-first CSS styling and iconography.
* `react-hook-form` & `zod`: Client-side form state management and schema validation.
* `recharts`: SVG charting library for analytics dashboards.
* `sonner`: Toast notification manager.

---

## 17. Environment & Configuration

| Variable Name | Purpose | Example / Default | Required |
|---|---|---|:---:|
| `ENVIRONMENT` | Runtime mode (`development`, `production`, `test`) | `development` | No |
| `DATABASE_URL` | Async PostgreSQL connection string | `postgresql+asyncpg://user:pass@postgres:5432/learnflow` | Yes |
| `REDIS_URL` | Redis connection URL for queues, rate limiting, and pub/sub | `redis://redis:6379/0` | Yes |
| `CHROMA_HOST` / `CHROMA_PORT` | ChromaDB vector store host and port | `chromadb` / `8000` | Yes |
| `JWT_SECRET` | Secret key used to sign and verify HS256 JWT tokens | `min-32-char-random-string` | **Yes** |
| `WEBHOOK_SIGNING_SECRET` | HMAC secret for signing and verifying n8n webhooks | `min-32-char-random-string` | **Yes** |
| `N8N_ENCRYPTION_KEY` | Encryption key for n8n credentials database | `random-hex-string` | **Yes** |
| `LLM_PROVIDER` | Active LLM provider (`mock`, `openai`, `azure_openai`, `ollama`) | `mock` | No |
| `OPENAI_API_KEY` | API key for OpenAI services | `sk-...` | If OpenAI used |
| `LLM_MODEL` | Default model for text generation | `gpt-4o-mini` | No |
| `EMBEDDING_PROVIDER` | Active embedding provider (`mock`, `openai`, `ollama`) | `mock` | No |
| `N8N_BASE_URL` | Internal URL to reach n8n container | `http://n8n:5678` | No |
| `PUBLIC_API_BASE_URL` | Base API URL reachable by n8n callbacks | `http://api:8000` | No |
| `UPLOAD_DIR` | Filesystem path for uploaded training binaries | `/data/uploads` | No |

---

## 18. Deployment Architecture

```
                                  KUBERNETES CLUSTER / DOCKER COMPOSE
                                 
                                     ┌───────────────────────────┐
                                     │      Ingress / Nginx      │
                                     └─────────────┬─────────────┘
                                                   │
                         ┌─────────────────────────┴─────────────────────────┐
                         │                                                   │
                         ▼ :3000                                             ▼ :8000
             ┌───────────────────────┐                           ┌───────────────────────┐
             │    learnflow-web      │                           │     learnflow-api     │
             │ (Next.js 15 Pod / Svc)│                           │ (FastAPI App Pod / Svc│
             └───────────────────────┘                           └───────────┬───────────┘
                                                                             │
                         ┌───────────────────────────────────────────────────┼───────────────────────────────────┐
                         │                                                   │                                   │
                         ▼                                                   ▼                                   ▼
             ┌───────────────────────┐                           ┌───────────────────────┐           ┌───────────────────────┐
             │    learnflow-worker   │                           │     PostgreSQL 16     │           │        Redis 7        │
             │   (ARQ Worker Pod)    │                           │ (StatefulSet + PVC)   │           │ (StatefulSet + PVC)   │
             └───────────┬───────────┘                           └───────────────────────┘           └───────────────────────┘
                         │                                                   ▲                                   ▲
                         │                                                   │                                   │
                         └───────────────────────────────────────────────────┴───────────────────────────────────┘
                                                   │
                         ┌─────────────────────────┴─────────────────────────┐
                         │                                                   │
                         ▼                                                   ▼
             ┌───────────────────────┐                           ┌───────────────────────┐
             │       ChromaDB        │                           │          n8n          │
             │ (StatefulSet + PVC)   │                           │ (StatefulSet + PVC)   │
             └───────────────────────┘                           └───────────────────────┘
```

* **Local Development:** `docker compose up --build` launches 7 containers (`postgres`, `redis`, `chromadb`, `api`, `worker`, `web`, `n8n`) with built-in healthcheck dependencies and automated database migrations before API startup.
* **Kubernetes (Production):** The `infrastructure/kubernetes/` folder contains 23 manifests managed via Kustomize:
  * Stateless services (`api`, `web`, `worker`) run as scalable `Deployments`.
  * Stateful datastores (`postgres`, `redis`, `chromadb`, `n8n`) run as `StatefulSets` with dedicated `PersistentVolumeClaims`.
  * Document uploads are mounted via a shared volume (`/data/uploads`).

---

## 19. Resource Consumption & Bottlenecks

1. **AI Token Consumption (Highest Variable Cost):**
   * Document ingestion: Embedding 100 pages of text consumes $\sim 80,000$ input tokens.
   * Course generation: Generates up to $6,000$ output tokens per course curriculum.
   * Assessment evaluation: Grading an essay uses $\sim 800\text{--}1,500$ prompt tokens and $300$ output tokens.
2. **Memory Footprint:**
   * FastAPI + Uvicorn: $\sim 150\text{ MB}$ base RSS.
   * Next.js 15: $\sim 120\text{ MB}$ base RSS.
   * ChromaDB: Grows with vector collection size; vector indexes are memory-mapped ($500\text{ MB}\text{--}2\text{ GB}$).
3. **Primary Scalability Bottlenecks (Ranked):**
   1. **ChromaDB Single-Node Architecture:** Embedded Chroma running over HTTP becomes CPU/memory-bound under heavy concurrent queries.
   2. **Local Volume File Sharing:** The API writes uploaded files to `/data/uploads` and the ARQ worker reads them. In Kubernetes, this requires a `ReadWriteMany` (RWX) storage class.
   3. **PostgreSQL Keyword Scan:** `document_chunks.content ILIKE '%term%'` requires sequential or B-tree scans that degrade on tables with $>500,000$ chunk rows.

---

## 20. Feasibility & System Ratings

| Metric | Score (1–10) | Detailed Justification |
|---|:---:|---|
| **Technical Feasibility** | **9 / 10** | Fully functioning implementation; all core features are covered by 59 backend and 10 frontend tests. |
| **Maintainability** | **8 / 10** | Clean folder structure, strict domain separation, typed Pydantic models, and centralized AI Gateway. |
| **Scalability** | **6 / 10** | Stateless API and workers scale horizontally, but ChromaDB and shared filesystem uploads are single-node bottlenecks. |
| **Reliability** | **8 / 10** | Idempotent webhooks, fallback Python runners for n8n, automatic LLM retries, and transactional middleware. |
| **Security** | **8 / 10** | Strong tenant isolation at the repository layer, single-use refresh tokens, magic-byte checks, and secret redaction. |
| **Performance** | **7 / 10** | Async I/O throughout, but RAG queries incur sequential latency (embed $\rightarrow$ vector query $\rightarrow$ keyword scan $\rightarrow$ LLM generation). |
| **Cost Efficiency** | **9 / 10** | Objective questions grade at zero cost in code; hard retrieval threshold ($0.25$) prevents token spend on ungrounded queries. |
| **Production Readiness** | **7 / 10** | Ready for pilot deployment; needs object storage (S3), managed Postgres/Redis, and SSO before enterprise enterprise adoption. |

---

## 21. Scalability: Load Projections & Bottlenecks

### 10 Users
* System operates effortlessly on a single $2\text{ vCPU} / 4\text{ GB RAM}$ virtual machine. Latencies are dominated by external LLM provider API response times ($400\text{--}1500\text{ ms}$).

### 100 Users
* ARQ's default 5-worker concurrency easily handles the background queue. Database connection pool ($20$ connections) handles API traffic. Memory consumption remains $< 2\text{ GB}$.

### 1,000 Users
* **First Pinch Point:** Rate limiting (120 req/min) starts rejecting active users sharing corporate proxy IPs.
* **Mitigation:** Switch rate limit identity from client IP to authenticated User UUID; scale ARQ workers to 3 replica pods.

### 10,000 Users
* **Bottlenecks Emerge:**
  1. `document_chunks` table exceeds $1\text{M}$ rows; PostgreSQL `ILIKE` keyword passes cause query latency spikes.
  2. ChromaDB HTTP container experiences lock contention during concurrent ingestion upserts and RAG queries.
  3. Shared upload disk I/O saturates.
* **Required Changes:**
  * Replace PostgreSQL `ILIKE` with PostgreSQL GIN Full-Text Index (`tsvector`).
  * Migrate from standalone ChromaDB container to managed pgvector or Qdrant cluster.
  * Move `/data/uploads` to Amazon S3 / Google Cloud Storage with pre-signed upload URLs.

### 100,000 Users
* **Architectural Overhaul Required:**
  * Implement Redis semantic response caching for popular knowledge base queries.
  * Split the database into read replicas for analytics/reporting and primary for writes.
  * Introduce message broker partitioning (e.g., Kafka or Redis Streams) with auto-scaling worker groups.

---

## 22. Failure Modes & Resilience

| Potential Failure | System Behavior | Recovery Mechanism |
|---|---|---|
| **LLM Provider Outage** | Gateway catches `5xx` / timeouts, retries with exponential backoff, then switches to `llm_fallback_provider`. | If all fail, raises `ProviderError` (502) and saves failure status to `llm_calls`. |
| **n8n Container Crash** | Worker attempts webhook call, catches `N8NUnavailable`, logs warning, and switches to built-in Python runner. | Execution succeeds via local Python code; records `source="builtin"`. `sync_n8n_executions` cron cleans up orphaned runs. |
| **Redis Outage** | `RateLimitMiddleware` logs warning and **degrades open** (allows traffic rather than taking API down). | Real-time SSE streaming drops; ARQ workers pause until Redis reconnects. |
| **Database Commit Failure** | `UnitOfWorkMiddleware` catches exception, rolls back transaction, and returns JSON 500 *before* headers leave. | Client receives clean 500 error; no partial or corrupted data is committed. |
| **Ingestion Pipeline Crash** | Pipeline catches stage error, marks `documents.status="failed"`, and records error in `pipeline_runs`. | Prior stage results remain intact; user can call `POST /reindex?from_stage={failed_stage}` to resume without re-uploading. |
| **Webhook Replay Attack** | Inbound webhook signature is inserted into `webhook_receipts.signature` (unique constraint). | Duplicate signature triggers database `IntegrityError`; API returns `401 Unauthorized` immediately. |

---

## 23. Security Model

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Inbound HTTP Request                            │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
       ┌────────────────────────┐    ┌────────────────────────┐
       │ Bearer <JWT Access>    │    │ X-API-Key: lf_...      │
       │ (Expires in 30 mins)   │    │ (SHA-256 Hashed in DB) │
       └────────────┬───────────┘    └────────────┬───────────┘
                    │                             │
                    ▼                             ▼
       ┌──────────────────────────────────────────────────────┐
       │ Verify Active Membership in Requested Organization   │
       └──────────────────────────┬───────────────────────────┘
                                  │
                                  ▼
       ┌──────────────────────────────────────────────────────┐
       │ Resolve Principal: {org_id, role, user_id, learner_id│
       └──────────────────────────┬───────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
       ┌─────────────────────────┐ ┌─────────────────────────┐
       │ Staff Role Context      │ │ Learner Role Context    │
       │ Enforces RBAC checks    │ │ Scoped ONLY to self     │
       │ via ROLE_PERMISSIONS    │ │ (Other learners -> 404) │
       └────────────┬────────────┘ └────────────┬────────────┘
                    │                           │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
       ┌──────────────────────────────────────────────────────┐
       │ TenantRepo Data Layer Binding                        │
       │ Appends `WHERE organization_id = :org_id` to queries │
       └──────────────────────────────────────────────────────┘
```

1. **Authentication:**
   * **Passwords:** Pre-hashed with SHA-256 before bcrypt (cost 12) to eliminate bcrypt's silent 72-byte truncation flaw.
   * **JWTs:** 30-minute access tokens signed with HS256. Roles are re-verified against the database on each request to ensure immediate revocation.
   * **Refresh Tokens:** Cryptographically random (48 bytes), stored as SHA-256 hashes, valid for 14 days, and single-use (redemption immediately revokes the token).
   * **API Keys:** Formatted as `lf_<token>`, stored only as SHA-256 hashes, displayed to the user exactly once at creation.
2. **Tenant Isolation:**
   * Data access must go through `TenantRepo`. Handing a non-tenant model to `TenantRepo` raises `TenantIsolationError`.
   * Cross-tenant read requests return `404 Not Found` (identical to non-existent resources) to prevent ID enumeration.
3. **Learner Self-Scoping:**
   * Learners are isolated within their organization. If a learner requests records belonging to another learner, the API returns a `404 Not Found`.
4. **Webhook Security:**
   * Signatures use `X-LearnFlow-Signature: t=<timestamp>,v1=<hmac>`.
   * Verification uses `hmac.compare_digest` (constant-time) with a 300-second timestamp tolerance window.
   * Signatures are written to a unique database column to reject replay attempts.
5. **Secret Redaction:**
   * Structlog filters redact any keys matching `password`, `secret`, `token`, `api_key`, `authorization`, `signature`, or `key_hash` before writing to log sinks.

---

## 24. Technical Debt & Areas for Refactoring

1. **Fixed-Window vs. Sliding-Window Rate Limiting (`apps/api/learnflow/middleware.py`):**
   * *Current State:* Rate limiting uses a simple Redis counter per minute bucket (`rl:{id}:{minute}`). A burst of requests spanning across the 59th and 01st second can exceed the intended threshold.
   * *Ideal Refactoring:* Implement a Redis Lua script utilizing a sliding-log or token-bucket algorithm.
2. **PostgreSQL `ILIKE` for Keyword Retrieval (`apps/api/learnflow/rag/retrieval.py`):**
   * *Current State:* Keyword matching uses substring `ILIKE` scans over chunk contents.
   * *Ideal Refactoring:* Implement PostgreSQL Full-Text Search with a generated `tsvector` column and a GiST/GIN index, or integrate a dedicated BM25 index.
3. **Shared Filesystem Volume for Ingestion (`apps/api/learnflow/rag/pipeline.py`):**
   * *Current State:* Uploaded files write to `/data/uploads` on local disk, requiring `ReadWriteMany` volumes in multi-node Kubernetes clusters.
   * *Ideal Refactoring:* Introduce an `ObjectStorageService` abstraction backing Amazon S3, Google Cloud Storage, or MinIO with pre-signed upload URLs.
4. **Synchronous RAG Response Streaming:**
   * *Current State:* SSE is used for pipeline and workflow progress, but `/rag/query` and `/assistant/ask` wait for the complete LLM response before returning.
   * *Ideal Refactoring:* Implement token-by-token streaming via SSE directly from the AI Gateway to the frontend client.

---

## 25. What Is Actually Technically Impressive

### Genuine Engineering Complexity (Highlight in Interviews)
1. **The Multi-Stage Resumable Pipeline:** Building a 10-stage pipeline with attempt counters, stage-level failure logging, and the ability to resume execution from intermediate stages (`from_stage="chunking"`) without re-parsing raw files.
2. **Pure ASGI `UnitOfWorkMiddleware`:** Intercepting low-level ASGI messages (`http.response.start`) to commit SQLAlchemy transactions prior to sending response bytes, resolving the classic FastAPI `yield` dependency race condition.
3. **Deterministic Human-in-the-Loop Evaluation Split:** Designing an evaluation engine that scores objective questions in code at $1.0$ confidence with zero token cost, routes subjective questions to LLMs with score clamping, and automatically triggers human review for low-confidence evaluations.
4. **Replay-Protected Webhook Subsystem:** Implementing constant-time HMAC-SHA256 verification with tolerance windows and database-backed unique constraints, paired with local Python fallback runners when n8n is offline.
5. **Learner Self-Scoping Security Model:** Enforcing identity boundaries server-side so that student accounts cannot enumerate or access sibling data, returning indistinguishable 404s.

### Standard Framework Features (Do Not Overstate)
* JWT creation and validation (`PyJWT` handles the cryptographic decoding).
* Password hashing (`bcrypt` handles salt generation and key stretching).
* OpenAPI documentation generation (native FastAPI functionality).
* Basic CRUD endpoints and database migrations (`FastAPI` + `Alembic`).

---

## 26. What I Would Improve in the Next Version

1. **RAG Evaluation Harness (Golden Dataset):** Implement an automated evaluation pipeline measuring Context Recall, Context Precision, and Faithfulness across prompt versions using tools like Ragas or TruLens.
2. **Cross-Encoder Reranker:** Add a lightweight reranking step (e.g., `bge-reranker-base` or Cohere Rerank) after RRF hybrid retrieval to improve precision at $K=3$.
3. **S3-Compatible Object Storage:** Decouple uploads from local disk by streaming multipart uploads directly to S3/MinIO.
4. **Token Streaming for Chat:** Upgrade the Learner Assistant to stream completions token-by-token using SSE.
5. **Granular API Key Scopes:** Enforce route-level scopes on API keys (e.g., `documents:read`, `evaluations:write`) instead of granting full `org_admin` permissions.

---

## 27. Technical Concepts to Master for Interviews

### Must Know (Critical)
* **Asyncio & ASGI Middleware Lifecycle:** Understand why standard `BaseHTTPMiddleware` causes post-response commit race conditions and how pure ASGI `__call__(scope, receive, send)` works.
* **Hybrid Search & Reciprocal Rank Fusion (RRF):** Be able to write the RRF formula on a whiteboard and explain why dense vector search fails on exact product SKUs and error codes.
* **Multi-Tenant Isolation Strategies:** Understand the security differences between table-level filtering, metadata vector filtering, and dedicated collection partitioning.
* **Cryptographic Signatures & Replay Defenses:** Explain how timestamped HMAC-SHA256 signatures prevent man-in-the-middle modifications and how unique receipt stores prevent replay attacks.

### Should Know (Important)
* **Token Budget Packing & Chunk Overlap:** How sentence-aware chunking preserves cross-boundary semantic context compared to naive character or token slicing.
* **Single-Use Rotating Refresh Tokens:** Why refresh tokens must be hashed in the database and how single-flight frontend logic prevents concurrency race conditions.
* **Structured Output Validation & Self-Repair:** How to prompt an LLM with Pydantic JSON schemas and implement automated error repair loops at `temperature=0.0`.

### Nice to Know (Bonus)
* **Kubernetes Storage Classes:** The difference between `ReadWriteOnce` (RWO) and `ReadWriteMany` (RWX) persistent volumes.
* **Fixed Window vs. Token Bucket Rate Limiting:** Tradeoffs between Redis atomic string counters and sliding window Lua scripts.

---

## 28. Technical Interview Questions

### Basic
1. *What is the primary role of the `TenantRepo` class in the backend?*
2. *Why does the application use both PostgreSQL and ChromaDB instead of just one database?*
3. *What happens when an unauthenticated request attempts to access `/api/v1/courses`?*
4. *How does the system know which role a user has when they make a request?*

### Intermediate
5. *Why are objective assessment questions graded in Python code instead of passing them to the LLM?*
6. *Explain the step-by-step process of the document ingestion pipeline. What happens if stage 7 (embedding) fails?*
7. *How does the frontend handle token refreshing when multiple API requests return 401 simultaneously?*
8. *Why did you choose Reciprocal Rank Fusion over standard dense vector retrieval?*
9. *What is the difference between how a Staff member and a Learner interact with the API?*

### Difficult & Architectural
10. *Why did you implement custom ASGI middleware for the Unit of Work instead of using FastAPI's standard `Depends(get_session)` with a `yield` commit?*
11. *Walk me through the security mechanics of an inbound webhook from n8n. How do you prevent replay attacks and timing attacks?*
12. *How does the system calculate per-execution LLM costs when multiple workflow runs are executing concurrently within the same organization?*
13. *If ChromaDB goes down completely, which parts of the application continue to function, and which parts fail?*
14. *Why did you pre-hash passwords with SHA-256 before feeding them to bcrypt?*

---

## 29. Model Interview Answers

#### On Unit of Work Middleware:
> *"In FastAPI, when you use a database session dependency with `yield`, the teardown code after the `yield` runs in a background task after the response has already been sent to the client. This creates a race condition: if a user registers and their client immediately fires a follow-up request to fetch profile data, the database transaction might not have committed yet, causing an intermittent 404.*
> 
> *To fix this, I implemented `UnitOfWorkMiddleware` as a pure ASGI middleware. It intercepts the `http.response.start` message and explicitly awaits `session.commit()` before sending response headers. If the commit fails, we roll back and return a 500 error before the client receives any data."*

#### On Assessment Auto-Grading & Human Gates:
> *"I designed the evaluation engine around a strict cost-efficiency and reliability principle. Objective questions—like multiple choice and true/false—are graded deterministically in Python code using exact-match normalization. This gives us 1.0 confidence at zero LLM cost.*
> 
> *For subjective short answers and scenarios, we call the AI Gateway with a structured grading rubric. The model's score is clamped to the question's allocated points to prevent hallucinated scales. If the minimum confidence score across any question falls below 0.75, the submission is marked `needs_review` and routed to a human reviewer queue. Crucially, API keys cannot approve evaluations—the endpoint enforces that only authenticated human users can sign off."*

#### On Webhook Security & Replay Prevention:
> *"Our webhook integration with n8n uses a three-layer defense. First, all payloads are signed with an HMAC-SHA256 digest formatted as `t=<timestamp>,v1=<mac>`. The server verifies this using `hmac.compare_digest` to prevent timing attacks.*
> 
> *Second, we enforce a 300-second freshness window to reject expired captured requests. Third, to stop replay attacks within that 5-minute window, we extract the signature and insert it into a `webhook_receipts` table that has a unique constraint on the signature column. If an attacker replays a valid, fresh payload, the database rejects the duplicate insert, stopping execution at the data layer."*

#### On Password Pre-Hashing:
> *"Bcrypt has a well-known limitation where it silently truncates passwords longer than 72 bytes. If a user provides an 80-character passphrase, any characters after the 72nd byte are ignored, which reduces entropy.*
> 
> *To prevent this, I pre-hash all passwords with SHA-256 and base64-encode the digest before passing it to bcrypt. This guarantees a consistent 44-character input string regardless of the original password length, while preserving full bcrypt salt and key-stretching benefits."*

---

## 30. The 20 Things You Must Know Before an Interview

1. **Monorepo Layout:** FastAPI backend in `apps/api`, Next.js 15 App Router frontend in `apps/web`.
2. **Primary Entry Points:** `main.py` for the API, `WorkerSettings` for the ARQ worker, `layout.tsx` for the frontend.
3. **Database Stack:** PostgreSQL 16 with async SQLAlchemy 2.0 and Alembic migrations. SQLite with in-memory tables is used for tests.
4. **Multi-Tenancy Model:** `TenantRepo` enforces `WHERE organization_id = :org_id` on all queries. Cross-tenant accesses return `404 Not Found`.
5. **Vector Isolation:** ChromaDB creates a dedicated collection per tenant named `org_<uuid.hex>` to prevent cross-tenant metadata leakage.
6. **Ingestion Pipeline:** 10 distinct stages (`upload` $\rightarrow$ `validation` $\rightarrow$ `extraction` $\rightarrow$ `cleaning` $\rightarrow$ `metadata` $\rightarrow$ `chunking` $\rightarrow$ `embedding` $\rightarrow$ `indexing` $\rightarrow$ `quality` $\rightarrow$ `completed`).
7. **Pipeline Resumability:** Failures are logged in `pipeline_runs`; pipelines can be resumed partway using `POST /documents/{id}/reindex?from_stage=...`.
8. **Magic Byte Checking:** Uploaded files are validated against binary headers (e.g., `%PDF-`), not just file extensions.
9. **Chunking Strategy:** Sentence-aware chunking packing up to 700 tokens with 100-token carried-forward overlap using `tiktoken`.
10. **Hybrid Retrieval:** Fuses dense vector similarity with a PostgreSQL `ILIKE` keyword pass using Reciprocal Rank Fusion ($k=60$).
11. **RAG Refusal Threshold:** If retrieval RRF score is $< 0.25$, the API refuses to answer, spending zero LLM generation tokens.
12. **AI Gateway:** Single choke point for model completions; handles retries, fallback provider chains, cost estimation, and structured JSON repair.
13. **Evaluation Human Gate:** Objective questions grade in code at 1.0 confidence; subjective questions score via LLM; overall confidence $< 0.75$ triggers a human reviewer queue.
14. **API Key Privilege Boundary:** API keys act as `org_admin` but are strictly forbidden from approving assessment evaluations.
15. **Learner Security Boundary:** Learner principals can only view their own enrollments and submissions; accessing other IDs returns a 404.
16. **n8n Webhook Security:** Timestamped HMAC-SHA256 signatures (`t=...,v1=...`), 300-second freshness window, and unique database receipt table for replay defense.
17. **n8n Offline Fallback:** If n8n is offline or unconfigured, built-in Python runners execute the exact same workflow steps locally.
18. **UnitOfWork Middleware:** Custom pure ASGI middleware that commits database sessions prior to sending HTTP response headers.
19. **Cost Attribution:** LLM spend is attributed to workflow executions via an explicit `workflow_execution_id` foreign key rather than timestamp windows.
20. **Deterministic Mock Mode:** Setting `LLM_PROVIDER=mock` allows the entire platform (including RAG retrieval and grading) to run end-to-end without API keys.

---

## 31. 30-Second Elevator Pitch

> *"LearnFlow AI is a multi-tenant AI workflow platform for corporate learning and development. It ingests company training materials into a citation-backed hybrid knowledge base, automatically generates modular courses and validated assessments, and auto-grades submissions under human supervision. 
> 
> Objective quiz items are graded instantly in Python code at zero AI cost, while free-text answers are evaluated by LLMs against rubrics—with anything under 75% confidence paused in a reviewer queue. Operations like onboarding and reminders are automated via HMAC-signed n8n webhooks with local Python fallbacks. The entire stack runs on Next.js 15, FastAPI, PostgreSQL, and Redis."*

---

## 32. 1-Minute Project Summary

> *"As corporate training programs grow, instructional designers spend weeks building courses, while instructors get buried under hundreds of open-ended assignments. LearnFlow AI automates this entire lifecycle while keeping humans in the loop.
> 
> When training documents are uploaded, a 10-stage background pipeline parses, validates, and indexes them into ChromaDB and PostgreSQL. We use a hybrid retrieval system—combining dense vector search with keyword matching using Reciprocal Rank Fusion. If retrieval confidence falls below our threshold, the system refuses to answer rather than hallucinating.
> 
> The platform generates structured courses and quizzes, running automated sanity checks to eliminate ambiguous questions. When learners submit assessments, objective questions are scored deterministically in code with 1.0 confidence, saving token costs, while essays are graded by an LLM with score clamping. Low-confidence scores enter a human reviewer queue.
> 
> Event-driven workflows—like sending welcome emails or nudging inactive students—are orchestrated through HMAC-signed n8n webhooks with built-in replay protection and local Python fallback runners. It is built using Next.js 15, FastAPI, SQLAlchemy, ARQ, and Redis."*

---

## 33. 3-Minute Technical Deep-Dive

> *"From an architectural perspective, LearnFlow AI is designed around four core principles: strict multi-tenant isolation, cost-conscious AI gating, human-in-the-loop validation, and resilient asynchronous automation.
> 
> At the presentation layer, we have Next.js 15 using App Router, separating staff administrative tools from the self-scoped learner portal. The backend is FastAPI running on a custom ASGI middleware stack. We avoided Starlette's standard `BaseHTTPMiddleware` because its background streaming task delays `yield` database dependency commits until after the response leaves the server. Instead, our `UnitOfWorkMiddleware` intercepts ASGI response start events to commit SQLAlchemy transactions before headers leave, guaranteeing read-after-write consistency.
> 
> For data isolation, our data access layer uses `TenantRepo`, which injects mandatory organization filters into every query and stamps tenant IDs on writes. In our vector store, ChromaDB creates isolated collections per tenant named `org_<uuid.hex>`, ensuring that an omitted query filter cannot leak cross-tenant embeddings.
> 
> Our RAG architecture uses a 10-stage background pipeline managed by ARQ and Redis, streaming live progress over Server-Sent Events. For retrieval, we combine dense cosine search with a PostgreSQL keyword pass using Reciprocal Rank Fusion ($k=60$). If the top chunks score below our $0.25$ threshold, we return a refusal immediately, saving generation tokens. All model calls pass through our centralized AI Gateway, which handles exponential backoff retries, secondary provider fallback, structured JSON validation with auto-repair prompts, and granular token cost accounting.
> 
> In our assessment engine, objective questions are graded in Python code with zero model overhead, while subjective questions are scored by LLMs against rubrics. If the aggregate confidence across questions is under 75%, the submission enters a reviewer queue where instructors can override scores; API keys are restricted from approving evaluations.
> 
> Finally, for automation, domain events trigger webhooks to an n8n cluster. We sign outbound and inbound requests with timestamped HMAC-SHA256 digests, enforce a 300-second freshness window, and store signatures in a unique database index to eliminate replay attacks. If n8n is unreachable, our built-in Python runners execute the exact same steps locally so the system degrades gracefully."*

---

## 34. Project Engineering Story

### 1. The Core Problem
Enterprise training teams struggle with manual curriculum authoring, subjective essay grading backlogs, and poor student engagement tracking. Existing AI solutions often hallucinate answers, expose multi-tenant data, or act as uncontrollable "black boxes" that grade students without human oversight.

### 2. The Simplest Naive Implementation
A basic implementation would be a single-tenant Next.js app calling OpenAI's `chat.completions` directly inside API routes, storing unstructured text chunks in a shared vector database, and asking GPT-4 to grade all quiz answers on every submission.

### 3. Why the Architecture Evolved
* **Preventing Data Leaks:** A single vector index with metadata filters risks leaking other companies' training data if a query forgets a filter. The architecture evolved to dedicated Chroma collections per tenant and repository-level data isolation.
* **Eliminating AI Waste:** Calling GPT-4 for multiple-choice questions is slow and expensive. The architecture shifted to deterministic code grading for objective questions and threshold-based refusals for ungrounded RAG queries.
* **Trust & Compliance:** Pure AI auto-grading is unacceptable for accredited enterprise compliance. We introduced confidence thresholds and a mandatory human reviewer queue.
* **Integration Reliability:** Relying on external webhook platforms can bring down a platform if the third-party engine crashes. We added HMAC replay defenses and local Python fallback runners.

### 4. Key Tradeoffs Made
* We chose **ARQ + Redis** over Celery for native `asyncio` integration, trading away complex workflow canvas features for codebase simplicity and shared database connection pools.
* We chose **pure ASGI middleware** over FastAPI's standard dependency injection for database commits, taking on lower-level byte handling to eliminate transactional race conditions.
* We chose **Reciprocal Rank Fusion (RRF)** over a neural cross-encoder reranker to keep retrieval latency low and eliminate GPU dependency requirements for local development.

### 5. Future Roadmap
The next iteration will focus on replacing the local filesystem upload volume with S3-compatible object storage, integrating a cross-encoder reranker for improved retrieval precision, adding token-by-token streaming to the Learner Assistant, and establishing an automated RAG evaluation test harness.
