# Architecture

## System overview

```mermaid
flowchart TB
    subgraph Client
        Browser["Next.js 15 dashboard<br/>App Router · TanStack Query · SSE"]
    end

    subgraph API["FastAPI"]
        MW["Middleware<br/>request id · rate limit · structured logs"]
        Auth["Auth + RBAC<br/>JWT · API keys · permission map"]
        Repo["TenantRepo<br/>refuses an unfiltered query"]
        Routers["/api/v1 routers"]
        Gateway["AI gateway<br/>retries · fallback · schema validation · cost"]
    end

    subgraph Data
        PG[("PostgreSQL<br/>tenant-scoped tables")]
        Redis[("Redis<br/>queue · pub/sub · rate limit")]
        Chroma[("ChromaDB<br/>one collection per org")]
        Files[("Upload volume")]
    end

    Worker["ARQ worker<br/>ingestion · generation · evaluation · workflows"]
    N8N["n8n<br/>5 exported workflows"]
    LLM["LLM providers<br/>OpenAI · Azure · HF · Ollama · mock"]

    Browser -->|REST| Routers
    Browser -->|Server-Sent Events| Routers
    Routers --> MW --> Auth --> Repo --> PG
    Routers --> Gateway --> LLM
    Routers -->|enqueue| Redis
    Redis --> Worker
    Worker --> PG
    Worker --> Chroma
    Worker --> Files
    Worker --> Gateway
    Worker -->|signed webhook| N8N
    N8N -->|signed callback| Routers
    Worker -->|publish progress| Redis
    Redis -->|subscribe| Routers
```

## Document ingestion flow

```mermaid
sequenceDiagram
    participant U as Admin
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Q as Redis / ARQ
    participant W as Worker
    participant V as ChromaDB

    U->>API: POST /documents (multipart)
    API->>API: validate extension, magic bytes, size, checksum
    API->>DB: INSERT document (pending)
    API->>API: write bytes to the upload volume
    API->>Q: enqueue ingest_document (idempotency key)
    API-->>U: 202 + document + job id

    W->>Q: claim job
    loop each of 10 stages
        W->>DB: INSERT pipeline_run (running)
        W->>W: upload → validation → extraction → cleaning → metadata<br/>→ chunking → embedding → indexing → quality → completed
        W->>Q: PUBLISH progress
        alt stage succeeds
            W->>DB: UPDATE pipeline_run (succeeded, duration, details)
        else stage fails
            W->>DB: UPDATE pipeline_run (failed, error)
            W->>DB: UPDATE document (failed) and stop
        end
    end
    W->>V: delete old vectors for this document, then upsert
    W->>DB: INSERT document_chunks, UPDATE document (indexed)

    U-->>API: GET /documents/{id}/stream (SSE)
    API-->>U: stage transitions as they happen
```

Re-running from a specific stage is a first-class operation:
`POST /documents/{id}/reindex?from_stage=chunking`.

## RAG request flow

```mermaid
sequenceDiagram
    participant U as User
    participant API as FastAPI
    participant G as AI gateway
    participant V as ChromaDB
    participant DB as PostgreSQL
    participant M as LLM provider

    U->>API: POST /rag/query
    API->>G: embed(question)
    G->>M: embedding request
    G->>DB: INSERT usage_record (embedding)
    API->>V: query(org collection, top_k * 2, metadata filter)
    API->>DB: keyword pass over document_chunks (ILIKE)
    API->>API: reciprocal rank fusion, drop below min_score

    alt nothing clears the threshold
        API-->>U: refusal, zero output tokens, no citations
    else context found
        API->>DB: load chunks + filenames for citations
        API->>G: complete(rag_answer prompt v3, numbered context)
        G->>M: chat completion (with retries and fallback)
        G->>DB: INSERT llm_call + usage_record
        API->>DB: INSERT messages (user, assistant with citations)
        API-->>U: answer + citations + confidence + tokens + cost + latencies
    end
```

## n8n workflow execution

```mermaid
sequenceDiagram
    participant S as Service (e.g. learner created)
    participant E as Event dispatcher
    participant DB as PostgreSQL
    participant Q as Redis / ARQ
    participant W as Worker
    participant N as n8n
    participant API as FastAPI webhook

    S->>E: emit("learner.created", payload)
    E->>DB: INSERT automation_event
    E->>DB: INSERT workflow_execution (queued, idempotency key)
    E->>Q: enqueue run_workflow

    W->>DB: UPDATE execution (running)
    alt workflow has an n8n webhook path
        W->>N: POST /webhook/{path} with X-LearnFlow-Signature
        N-->>W: 200 { executionId }
        W->>DB: store n8n_execution_id, stay "running"
        N->>N: run the nodes
        N->>API: POST /webhooks/n8n/callback (signed)
        API->>API: verify HMAC, check freshness, insert receipt (unique)
        API->>DB: apply steps, status, tokens; mark finished
    else n8n unreachable or not configured
        W->>W: run the built-in equivalent, recording every step
        W->>DB: steps, status, duration, attributed AI cost
    end
    W->>Q: PUBLISH execution updates → SSE to the browser
```

## Assessment evaluation

```mermaid
flowchart TD
    A[Learner submits answers] --> B{Question type}
    B -->|multiple choice / true-false| C[Exact match in code<br/>confidence 1.0 · no model call]
    B -->|short answer / scenario| D{Answer empty?}
    D -->|yes| E[Score 0, confidence 1.0]
    D -->|no| F[Grade against the rubric<br/>score clamped to question points]
    C --> G[Aggregate]
    E --> G
    F --> G
    G --> H{min confidence ≥ 0.75?}
    H -->|yes| I[ai_completed<br/>shown as provisional]
    H -->|no| J[needs_review<br/>emits evaluation.needs_review]
    J --> K[Reviewer queue]
    K --> L{Human decision}
    L -->|approve, optional score override| M[approved]
    L -->|reject| N[rejected]
```

An API key cannot approve an evaluation — the endpoint requires a user principal.

## Multi-tenant authorization

```mermaid
flowchart TD
    R[Request] --> C{Credential}
    C -->|X-API-Key| K[Hash lookup → org, org_admin role]
    C -->|Bearer JWT| J[Decode → user + org claim]
    C -->|neither| U401[401 Unauthorized]

    J --> M{Member of the organization?}
    M -->|no, not platform admin| F403[403 Forbidden]
    M -->|yes| P[Principal: org id + role]
    K --> P

    P --> O{Organization active?}
    O -->|no| F403
    O -->|yes| RB{Route permission?}

    RB -->|role lacks it| F403
    RB -->|granted| TR[TenantRepo bound to org id]

    TR --> Q[Every SELECT gets<br/>organization_id = :org]
    TR --> W[Every INSERT is stamped<br/>with the org id]
    TR --> D{Model is tenant-scoped?}
    D -->|no| E1[TenantIsolationError — a bug, not a 404]
    Q --> N404[Missing or other tenant → identical 404]
```

## Key technical decisions

| Decision | Alternative | Why this one |
|---|---|---|
| ARQ for background jobs | Celery | Async-native, same asyncio stack as FastAPI, no separate protocol layer |
| Isolation by Chroma **collection** | Metadata filter on a shared collection | A forgotten filter leaks other tenants' data; a wrong collection name returns nothing |
| No LangChain / LlamaIndex | Framework-first RAG | Retries, fallback, token accounting and prompt versioning are the interesting parts — hiding them behind a framework would defeat the purpose |
| SSE, not WebSockets | Bidirectional socket | Updates are one-directional; SSE survives any proxy and is ~15 lines |
| Every model call through one gateway | Call providers at each site | Single place for retry, fallback, cost, observability and schema validation |
| Built-in runners beside n8n | n8n only | Tests, CI and the first-run demo work with no n8n; production still routes through it |
| Execution cost by foreign key | Sum LLM calls in a time window | Concurrent runs in one org would otherwise each claim the other's tokens |
| Baseline migration from metadata | Hand-written `op.create_table` | The models are the source of truth for v1; transcribing 30 tables by hand only adds drift |
