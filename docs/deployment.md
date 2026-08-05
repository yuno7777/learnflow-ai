# Deployment

## Docker Compose

Development is the default:

```bash
cp .env.example .env
docker compose up --build
```

For a production-shaped run on a single host:

```bash
# 1. Real secrets
JWT_SECRET=$(openssl rand -hex 32)
WEBHOOK_SIGNING_SECRET=$(openssl rand -hex 32)
N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)

# 2. Production settings in .env
ENVIRONMENT=production
DEBUG=false
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_FALLBACK_PROVIDER=ollama        # keeps working through an OpenAI outage
EMBEDDING_PROVIDER=openai
SMTP_HOST=smtp.example.com          # unset means the mock adapter, which sends nothing
PUBLIC_API_BASE_URL=https://learnflow.example.com
NEXT_PUBLIC_API_URL=https://learnflow.example.com/api
CORS_ORIGINS=["https://learnflow.example.com"]
RATE_LIMIT_PER_MINUTE=300

# 3. Do not expose datastore ports publicly — put a TLS-terminating reverse proxy
#    in front of web (3000) and api (8000) and leave 5432/6379/8001 on the internal network.
docker compose up -d --build
docker compose exec api alembic upgrade head
```

`ENVIRONMENT != development` switches logging to JSON for ingestion by a log pipeline.

### Operational notes

- The `migrate` service runs to completion before `api` starts; `worker` and `web` wait for
  `api` to pass its health check.
- Volumes hold Postgres data, Redis AOF, Chroma, n8n credentials and uploads. Back all five
  up together — Chroma and Postgres must be consistent with each other or citations break.
- `docker compose down` keeps volumes; `down -v` destroys them.
- Scale the worker with `docker compose up -d --scale worker=3`. ARQ jobs are claimed once,
  and every job carries an idempotency key.

## Kubernetes

23 manifests under `infrastructure/kubernetes/`, applied with kustomize. Full detail in the
[manifests README](../infrastructure/kubernetes/README.md).

```bash
kubectl apply -f infrastructure/kubernetes/00-namespace.yaml

kubectl -n learnflow create secret generic learnflow-secrets \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=WEBHOOK_SIGNING_SECRET="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=N8N_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  --from-literal=OPENAI_API_KEY="sk-..."

kubectl apply -k infrastructure/kubernetes
kubectl -n learnflow get pods -w
```

Points worth noticing in the manifests:

- Migrations run as a **Job**, and the API Deployment has an init container that waits for it
  — pods never serve traffic against an un-migrated schema.
- Readiness hits `/health/ready`, which checks Postgres and Redis and returns **503** when
  either is down, so a pod with a broken dependency leaves the Service.
- The ingress disables proxy buffering and raises the read timeout, without which SSE updates
  arrive only when the buffer flushes.
- The example Secret manifest exists to document the shape and is deliberately excluded from
  the kustomization.

Building and pushing images:

```bash
docker build -t registry.example.com/learnflow/api:v0.1.0    --target base   apps/api
docker build -t registry.example.com/learnflow/worker:v0.1.0 --target worker apps/api
docker build -t registry.example.com/learnflow/web:v0.1.0 \
  --build-arg NEXT_PUBLIC_API_URL=https://learnflow.example.com/api apps/web
docker push registry.example.com/learnflow/{api,worker,web}:v0.1.0
```

Then update the image references (`kustomize edit set image`, or a values file if you convert
this to a Helm chart).

## Scaling

| Component | Scaling | Constraint |
|---|---|---|
| `web` | Horizontal, stateless | None |
| `api` | Horizontal, stateless | Needs RWX for the uploads volume |
| `worker` | Horizontal | Same RWX constraint; jobs are idempotent |
| `postgres` | Vertical, then read replicas | Single writer |
| `redis` | Vertical | Single instance in these manifests |
| `chromadb` | Vertical | Single node; move to a hosted vector store to scale out |
| `n8n` | Vertical | Queue mode with workers is possible but not configured here |

The upload volume is the real blocker on horizontal scale. Moving file storage to
S3-compatible object storage removes it and is a contained change — the storage path is a
single field on `documents`.

## Monitoring

- `/health` — liveness.
- `/health/ready` — readiness, 503 when Postgres or Redis is unreachable.
- `GET /api/v1/integrations` — live status of every dependency, rendered on the Integrations
  page.
- Structured JSON logs with a request id on every line; the same id appears on `llm_calls` and
  `audit_logs`.
- `GET /api/v1/analytics/usage` — token spend and failure counts per operation, which is the
  first place a provider problem shows up.

There is no Prometheus exporter or OpenTelemetry export; see
[limitations](limitations.md).
