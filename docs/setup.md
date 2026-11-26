# Local setup

## Option A — Docker (recommended)

Install Python 3.11+ and Docker Compose v2. Use Docker Desktop on Windows (WSL 2
backend) or macOS, and Docker Engine with the Compose plugin on Linux. Start Docker
before running the bootstrap.

Windows PowerShell:

```powershell
py -3 scripts/dev.py up
```

macOS and Linux:

```sh
python3 scripts/dev.py up
```

Run the command from the checkout. It creates `.env` with randomly generated
local secrets on first use, builds the services, and waits for startup. It reuses
an existing `.env`. To validate separately, run the same launcher with `init`
followed by `doctor` to check Docker connectivity and Compose configuration.

To load demo data into a local development stack, run
`docker compose exec api python -m learnflow.seed`.

The `migrate` service runs
`alembic upgrade head` and must complete before `api` starts; `api` must pass its health
check before `worker` and `web` start.

| Service | Port | Health check |
|---|---|---|
| postgres | 5432 | `pg_isready` |
| redis | 6379 | `redis-cli ping` |
| chromadb | 8001 | `/api/v1/heartbeat` |
| api | 8000 | `/health` |
| worker | — | none (ARQ has no HTTP surface) |
| web | 3000 | `/api/health` |
| n8n | 5678 | `/healthz` |

Optional local models:

```bash
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull llama3.1
# then in .env: LLM_PROVIDER=ollama, LLM_MODEL=llama3.1
docker compose up -d --force-recreate api worker
```

### Useful commands

```bash
docker compose logs -f api worker          # follow both
docker compose exec api alembic upgrade head
docker compose exec api python -m learnflow.seed --reset   # wipe and reseed the demo org
docker compose exec postgres psql -U learnflow -d learnflow
docker compose down -v                     # stop and delete volumes
```

## Option B — without Docker

You still need Postgres and Redis. ChromaDB can run embedded on disk.

```bash
# Backend
cd apps/api
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # Windows: .venv\Scripts\pip

export DATABASE_URL=postgresql+asyncpg://learnflow:learnflow@localhost:5432/learnflow
export REDIS_URL=redis://localhost:6379/0
export CHROMA_PERSIST_DIR=./.chroma        # embedded, no Chroma server needed
export UPLOAD_DIR=./.uploads
export JWT_SECRET=$(openssl rand -hex 32)
export WEBHOOK_SIGNING_SECRET=$(openssl rand -hex 32)

alembic upgrade head
python -m learnflow.seed
uvicorn learnflow.main:app --reload --port 8000
```

In a second terminal:

```bash
cd apps/api
arq learnflow.worker.main.WorkerSettings
```

In a third:

```bash
cd apps/web
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```

## Connecting n8n

1. Open http://localhost:5678 and create the owner account (first run only).
2. **Settings → n8n API → Create API key**, put it in `.env` as `N8N_API_KEY`. This lets
   LearnFlow read execution details back for reconciliation.
3. In LearnFlow: **Settings → API keys → Create key**, put it in `.env` as
   `LEARNFLOW_API_KEY`. This lets n8n call back into the API.
4. Import the workflows: **Workflows → Import from file**, one per file in
   `automation/n8n-workflows/`.
5. Activate each workflow.
6. In LearnFlow, create a workflow from the matching template and set its
   **n8n webhook path** (for example `learnflow/learner-onboarding`).
7. `docker compose up -d --force-recreate api worker n8n` to pick up the new env values.

Without step 6, workflows still run — the built-in runner executes the equivalent steps and
the execution row records `engine: builtin`.

## Troubleshooting

**`docker compose up` exits with "set JWT_SECRET in .env"** — the compose file refuses to
start without the secrets rather than silently using a default. `cp .env.example .env` and
fill in the three generated values.

**Documents stay `pending`** — the worker is not running or cannot reach Redis.
`docker compose logs worker`.

**RAG always refuses** — the documents are not indexed yet (check the Documents page), or
`RETRIEVAL_MIN_SCORE` is too high for your embedding model. The mock embedder scores lower
than a real one; 0.25 is tuned for it.

**`MissingGreenlet` in a stack trace** — something touched a lazy relationship on an async
session. Load it with `selectinload`, or build the response model from the flat serializer.

**n8n Code node fails with "crypto is not defined"** — set
`NODE_FUNCTION_ALLOW_BUILTIN=crypto` (already set in `docker-compose.yml`).
