# Kubernetes manifests

Plain YAML, applied with kustomize. This demonstrates a working single-cluster deployment —
it is not a production-hardened chart (see [Known limitations](#known-limitations)).

```bash
# 1. Create the namespace and the secret (never commit real values)
kubectl apply -f 00-namespace.yaml
kubectl -n learnflow create secret generic learnflow-secrets \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=WEBHOOK_SIGNING_SECRET="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=N8N_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
  --from-literal=OPENAI_API_KEY=""

# 2. Everything else
kubectl apply -k .

# 3. Watch it come up
kubectl -n learnflow get pods -w
```

## What is here

| File | Contents |
|---|---|
| `00-namespace.yaml` | Namespace |
| `01-config.yaml` | ConfigMap of non-secret settings |
| `02-secrets.example.yaml` | Shape of the Secret — **example only, do not apply as-is** |
| `10-postgres.yaml` | StatefulSet + PVC + headless Service |
| `11-redis.yaml` | Deployment + PVC + Service |
| `12-chromadb.yaml` | Deployment + PVC + Service |
| `20-api.yaml` | API Deployment, Service, migration Job, uploads PVC |
| `21-worker.yaml` | ARQ worker Deployment |
| `22-web.yaml` | Next.js Deployment + Service |
| `23-n8n.yaml` | n8n Deployment + PVC + Service |
| `30-ingress.yaml` | Ingress routing `/` to web and `/api` to the API |
| `kustomization.yaml` | Ties it together |

## Design notes

- The migration runs as a **Job with a `helm.sh/hook`-style ordering**: the API Deployment has
  an init container that waits for the Job to complete, so pods never serve traffic against an
  un-migrated schema.
- `readinessProbe` hits `/health/ready`, which checks Postgres and Redis and returns **503**
  when either is down — so a pod with a broken dependency is pulled from the Service.
- Uploads use a `ReadWriteMany` PVC shared by the API and worker. On a cluster without RWX
  storage, replace it with object storage (see Known limitations).

## Known limitations

- Single-replica Postgres with a PVC, not an operator or managed service. Fine for a demo,
  not for production durability.
- Uploads need `ReadWriteMany`. Many default StorageClasses only offer `ReadWriteOnce`; on
  those, run the API and worker on one node or move file storage to S3-compatible object
  storage.
- No HorizontalPodAutoscaler, PodDisruptionBudget, NetworkPolicy or resource quotas.
- TLS is left to the ingress controller (cert-manager annotations are commented in place).
