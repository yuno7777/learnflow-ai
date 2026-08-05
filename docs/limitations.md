# Known limitations and future work

An honest account of what this system is not. Everything claimed in the README is
implemented and tested; everything below is not.

## Scale and operations

- **Single-node datastores.** Postgres, Redis and Chroma each run as one instance with a
  volume. No replication, no failover, no backup automation. Production means managed
  Postgres, Redis with persistence and failover, and a hosted vector store.
- **Uploads on a shared volume.** The API writes, the worker reads. In Kubernetes that
  requires `ReadWriteMany`, which many default StorageClasses do not offer. Object storage
  (S3-compatible) is the right answer; the storage path is already a single field on
  `documents`, so the change is contained.
- **Rate limiting is a fixed window** in Redis, not a sliding window or token bucket, so a
  burst can straddle the boundary. Marked in the code with a `ponytail:` comment.
- **No distributed tracing.** Structured logs carry a request id, and every LLM call and audit
  entry records it, but there is no OpenTelemetry export.
- **Worker concurrency is fixed** at five jobs per process with no autoscaling and no priority
  queues; a large ingestion can delay a small evaluation.

## AI

- **Cost is estimated, not billed.** Prices come from a static table in `ai/pricing.py`; models
  not in it fall back to a conservative guess. Reconcile against provider invoices.
- **No semantic caching.** Identical questions re-embed and re-answer.
- **No reranker.** Hybrid fusion is reciprocal rank fusion over dense and keyword hits; a
  cross-encoder reranker would measurably improve precision at top_k.
- **No evaluation harness for the RAG itself.** There is no golden question set measuring
  retrieval precision or answer faithfulness over time — the tests prove citations exist and
  refusal works, not that answers are good.
- **Tool calling needs a provider that supports it.** The mock provider does not emit tool
  calls, so the learner assistant grounds via a forced first knowledge-base search and then
  answers; with OpenAI it uses the full tool loop.
- **No streaming token output.** Responses are returned whole. SSE is used for pipeline and
  workflow progress, not for token-by-token generation.
- **Prompt versions are code, not data.** The `prompt_templates` table exists for per-org
  overrides but the UI to edit them is not built.

## Security

- No MFA, no SSO (SAML/OIDC), no password reset flow — there is no mail server in the base
  setup, so new members get an explicitly-set initial password.
- **API key scopes are stored but not enforced per route.** A key currently acts as
  `org_admin` within its organization.
- No virus scanning on uploads.
- No field-level encryption at rest; delegated to disk or database encryption.
- No per-tenant encryption keys, no data residency controls.

## Product

- **The workflow builder is templates, not a canvas.** Seven templates map onto the exported
  n8n workflows. Deliberate — replicating the n8n editor was explicitly out of scope.
- **No learner-facing portal.** Learners exist as records; submissions are made through the
  admin UI for demonstration. A separate learner app is the obvious next surface.
- **No certificates, SCORM/xAPI, or LTI integration.**
- **Content transformation returns job results via the jobs API**, without a dedicated UI to
  browse generated flashcards and FAQs.
- **Analytics filters are partial.** Date range and metric filters are wired; workspace,
  course and model filters exist on the API but are not all exposed in the UI.
- **English-oriented.** Language detection is a stopword vote; there is no UI localisation.

## Testing

- 49 backend and 10 frontend tests cover authentication, tenant isolation, ingestion, RAG,
  generation, validation, evaluation, workflows, webhooks, seeding and analytics.
- **Playwright tests are opt-in** (`E2E_RUN=1`) because they need a running, seeded stack.
- **No load testing.** Behaviour under concurrent ingestion of large PDFs is unmeasured.
- **The vector store is exercised in-memory in tests**, not against real Chroma. The adapter
  boundary is thin, but it is a real gap.

## What I would build next, in order

1. **A RAG evaluation harness** — a golden question set with retrieval precision and answer
   faithfulness tracked per prompt version. Right now prompt changes ship on judgement alone.
2. **Object storage for uploads**, which removes the RWX requirement and unblocks horizontal
   scaling of the worker.
3. **Enforced API key scopes**, so an integration key can be read-only.
4. **A reranker** in the retrieval path, measured against the harness from (1).
5. **A learner portal**, which is where the product value actually lands.
