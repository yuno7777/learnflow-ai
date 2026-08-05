# Environment variables

Every setting is read from the environment (`apps/api/learnflow/config.py`). `.env.example`
is the template; `.env` is gitignored.

Compose refuses to start without `JWT_SECRET`, `WEBHOOK_SIGNING_SECRET` and
`N8N_ENCRYPTION_KEY` — a missing secret should fail loudly, not silently fall back to a
default that ends up in production.

## Core

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | Anything else switches logs to JSON |
| `DEBUG` | `false` | Echoes SQL when true. Never enable in production |
| `API_V1_PREFIX` | `/api/v1` | |

## Secrets

| Variable | Generate with | Effect if wrong |
|---|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` | Changing it invalidates every access token |
| `WEBHOOK_SIGNING_SECRET` | `openssl rand -hex 32` | Must match `LEARNFLOW_WEBHOOK_SECRET` in n8n, or every callback 401s |
| `N8N_ENCRYPTION_KEY` | `openssl rand -hex 32` | Changing it makes n8n unable to decrypt stored credentials |

## Datastores

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://learnflow:learnflow@localhost:5432/learnflow` | Must be the **asyncpg** driver |
| `REDIS_URL` | `redis://localhost:6379/0` | Queue, pub/sub and rate limiting |
| `CHROMA_HOST` / `CHROMA_PORT` | `localhost` / `8001` | HTTP client |
| `CHROMA_PERSIST_DIR` | unset | When set, Chroma runs **embedded on disk** and the host/port are ignored — useful for local dev and CI |

## Authentication

| Variable | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_TTL_MINUTES` | `30` | Shorter is safer; the frontend refreshes automatically |
| `REFRESH_TOKEN_TTL_DAYS` | `14` | Single-use rotation regardless |
| `PASSWORD_MIN_LENGTH` | `10` | Enforced on the server; the register form mirrors it |
| `JWT_ALGORITHM` | `HS256` | |

## AI providers

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `openai`, `azure_openai`, `huggingface`, `ollama` |
| `LLM_MODEL` | `gpt-4o-mini` | |
| `LLM_FALLBACK_PROVIDER` | unset | Used automatically when the primary fails after its retries |
| `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` | `mock` / `text-embedding-3-small` | Changing the embedding model requires re-indexing every document |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the model |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | unset / OpenAI | The base URL also serves vLLM, LM Studio and TGI |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_VERSION` | unset / `2024-08-01-preview` | Deployment name goes in `LLM_MODEL` |
| `HUGGINGFACE_BASE_URL` / `HUGGINGFACE_API_KEY` | unset | Any OpenAI-compatible inference endpoint |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Uses the native API so schema-constrained output works |
| `LLM_TIMEOUT_SECONDS` | `90` | |
| `LLM_MAX_RETRIES` | `3` | Per provider, before falling back |

`mock` is fully offline and deterministic: schema-valid structured output and hash-based
embeddings. Start here; nothing needs configuring and no calls leave the machine.

## Retrieval

| Variable | Default | Notes |
|---|---|---|
| `CHUNK_SIZE_TOKENS` | `700` | Per-knowledge-base override available |
| `CHUNK_OVERLAP_TOKENS` | `100` | Clamped to half the chunk size |
| `RETRIEVAL_TOP_K` | `6` | Passages sent to the model |
| `RETRIEVAL_MIN_SCORE` | `0.25` | Below this, the assistant refuses. Tuned for the mock embedder; raise it for a real one |
| `MAX_UPLOAD_BYTES` | `26214400` (25 MB) | Also enforce it at the reverse proxy |
| `UPLOAD_DIR` | `/data/uploads` | Must be shared between API and worker |

## Automation

| Variable | Default | Notes |
|---|---|---|
| `PUBLIC_API_BASE_URL` | `http://localhost:8000` | Written into the callback URL sent to n8n — must be reachable **from n8n** |
| `N8N_BASE_URL` | `http://localhost:5678` | |
| `N8N_API_KEY` | unset | Needed only for execution reconciliation |
| `WEBHOOK_TOLERANCE_SECONDS` | `300` | Signature freshness window |

## Email

| Variable | Default | Notes |
|---|---|---|
| `SMTP_HOST` | unset | **Empty means the mock adapter** — messages are logged and retained in memory, never sent |
| `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | `587` / unset / unset | STARTTLS |
| `EMAIL_FROM` | `no-reply@learnflow.local` | |

## Operations

| Variable | Default | Notes |
|---|---|---|
| `CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array. Never `["*"]` with credentials |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per API key, bearer token or IP |

## Frontend

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_API_URL` | Baked in at build time; the **browser** must be able to reach it |
| `API_URL` | Server-side only; the internal address (`http://api:8000` in compose) |

## Precedence and gotchas

1. Real environment variables win.
2. Then `.env` in the working directory.
3. Then the defaults in `config.py`.

- Changing `EMBEDDING_MODEL` silently breaks retrieval until every document is re-indexed —
  old vectors are in a different space.
- `PUBLIC_API_BASE_URL` must be reachable **from n8n**, not from your laptop. Inside compose
  that is `http://api:8000`, not `http://localhost:8000`.
- `NEXT_PUBLIC_API_URL` is compiled into the client bundle; changing it needs a rebuild, not
  just a restart.
