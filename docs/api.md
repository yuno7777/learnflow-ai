# API reference

Base path: `/api/v1`. Interactive documentation at `/docs`, machine-readable schema at
`/openapi.json` (71 paths).

## Conventions

**Authentication.** Either a user token or a machine key:

```http
Authorization: Bearer <access token>
X-Organization-Id: <uuid>          # optional; defaults to the token's org claim
```
```http
X-API-Key: lf_...
```

**Pagination.** `?limit=25&offset=0&sort=-created_at` (prefix `-` for descending; unknown
sort keys fall back to `created_at` rather than erroring).

```json
{ "items": [], "total": 0, "limit": 25, "offset": 0 }
```

**Errors.** One shape everywhere:

```json
{
  "error": { "code": "conflict", "message": "...", "details": {} },
  "request_id": "8f2c1d..."
}
```

| Code | HTTP | Meaning |
|---|---|---|
| `unauthorized` | 401 | Missing, invalid or expired credentials |
| `forbidden` | 403 | Authenticated, but the role lacks the permission |
| `not_found` | 404 | Missing — or owned by another tenant (deliberately identical) |
| `conflict` | 409 | Duplicate file, key collision, illegal state transition |
| `validation_failed` | 422 | Request body or file failed validation |
| `rate_limited` | 429 | Per-principal limit exceeded; `Retry-After` set |
| `provider_error` | 502 | Upstream AI provider failed after retries and fallback |
| `internal_error` | 500 | Unhandled; details go to the log, never to the caller |

Every response carries `X-Request-ID`, echoed into logs and audit entries.

---

## Authentication

```bash
# Register — creates the user, the organization and an org_admin membership
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@acme.test","password":"Sup3rSecret!pass",
       "full_name":"Priya Raman","organization_name":"Acme Training"}'

# Login
curl -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@learnflow.dev","password":"LearnFlow!2026"}'
```

```json
{
  "access_token": "eyJ...",
  "refresh_token": "8Kx...",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": { "id": "…", "email": "admin@learnflow.dev", "full_name": "Priya Raman" },
  "organization_id": "…",
  "role": "org_admin",
  "memberships": [{ "organization_id": "…", "organization_name": "Acme Training", "role": "org_admin" }]
}
```

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | 201 |
| POST | `/auth/login` | Identical error for unknown email and wrong password |
| POST | `/auth/refresh` | **Single use** — the presented token is revoked as it is redeemed |
| POST | `/auth/logout` | 204 |
| GET | `/auth/me` | Principal, role, resolved permissions, and `learner_id` when the login is a learner |

---

## Knowledge base and documents

```bash
# Create a knowledge base
curl -X POST localhost:8000/api/v1/knowledge-bases -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Employee Handbook","chunk_size_tokens":700,"chunk_overlap_tokens":100}'

# Upload (202 — ingestion runs on the worker)
curl -X POST "localhost:8000/api/v1/documents?knowledge_base_id=$KB" -H "$AUTH" \
  -F file=@handbook.pdf

# Watch the pipeline
curl -N localhost:8000/api/v1/documents/$DOC/stream -H "$AUTH"
```

```
event: progress
data: {"stage":"embedding","status":"running","progress":0.68}

event: progress
data: {"stage":"completed","status":"succeeded","progress":1.0}

event: done
data: {"status":"indexed"}
```

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/knowledge-bases` | |
| DELETE | `/knowledge-bases/{id}` | Deletes documents and vectors too |
| GET | `/documents` | `?knowledge_base_id=&status=` |
| POST | `/documents` | multipart; 409 on a duplicate checksum |
| GET | `/documents/{id}` | Includes every pipeline stage attempt |
| GET | `/documents/{id}/chunks` | Inspect what was actually indexed |
| POST | `/documents/{id}/reindex` | `?from_stage=chunking` to resume |
| DELETE | `/documents/{id}` | Removes chunks, vectors and the stored file |
| GET | `/documents/{id}/stream` | SSE |

---

## RAG and assistants

```bash
curl -X POST localhost:8000/api/v1/rag/query -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the approval code for a remote work request?"}'
```

```json
{
  "answer": "Remote requests use approval code RW-2291 [1], and must be approved by a direct manager at least five business days in advance [1].",
  "refused": false,
  "citations": [{
    "chunk_id": "…", "document_id": "…", "document_name": "remote-work-policy.md",
    "snippet": "Employees may work remotely up to three days per week…",
    "score": 0.7412, "page": null, "section": "Remote Work Policy"
  }],
  "confidence": 0.7412,
  "model": "gpt-4o-mini", "provider": "openai",
  "input_tokens": 812, "output_tokens": 96, "estimated_cost_usd": 0.000179,
  "retrieval_latency_ms": 141, "llm_latency_ms": 903,
  "conversation_id": "…", "message_id": "…"
}
```

When nothing clears the similarity threshold the response is a refusal with
`"citations": []` and `"output_tokens": 0` — no model call is made at all.

| Method | Path | Notes |
|---|---|---|
| POST | `/rag/query` | Pass `conversation_id` to continue a thread |
| GET | `/conversations`, `/conversations/{id}` | |
| POST | `/conversations/{c}/messages/{m}/feedback` | `{"rating": 1 \| -1}` |
| POST | `/assistant/ask` | Learner assistant with tool calling |
| POST | `/content/transform` | Summaries, flashcards, FAQs, quizzes, posts (async) |
| GET | `/content/output-types` | |

---

### Learner scoping

A principal with the `learner` role is restricted server-side, not by hiding UI:

| Endpoint | What a learner sees |
|---|---|
| `GET /learners` | Exactly one row — themselves |
| `GET /learners/{id}` | 404 for anyone else, identical to a nonexistent id |
| `POST /learners/{id}/progress` | Their own enrolment only |
| `GET /courses` | Published courses they are enrolled in |
| `GET /assessments` | Published assessments only |
| `GET /assessments/{id}?include_answers=true` | 403 |
| `GET /submissions` | Their own submissions |
| `POST /assessments/{id}/submissions` | `learner_id` is forced to themselves |
| `POST /assistant/ask` | `learner_id` is forced to themselves |
| analytics, audit, keys, workflows | 403 |

`POST /learners/{id}/access` (needs `learner:manage`) creates or links a user account with
the learner role and points the learner record at it. Learner records and logins are separate
until someone does this.

## Courses and assessments

```bash
# Generate (202 — returns a course in status "generating" plus a job id)
curl -X POST localhost:8000/api/v1/courses/generate -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d "{\"knowledge_base_id\":\"$KB\",\"document_ids\":[\"$DOC\"],
       \"subject\":\"Remote work policy\",\"module_count\":4,\"audience\":\"new employees\"}"

curl -X POST localhost:8000/api/v1/courses/$COURSE/publish -H "$AUTH"
```

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/courses` | |
| POST | `/courses/generate` | 202 |
| GET/PATCH | `/courses/{id}` | Detail includes modules and lessons |
| POST | `/courses/{id}/publish` | 409 if it has no modules or is still generating |
| POST | `/courses/{id}/modules/{m}/regenerate` | 202 |
| PATCH | `/courses/{id}/lessons/{l}` | Edit generated content |
| POST | `/assessments/generate` | 202 |
| GET | `/assessments/{id}` | `?include_answers=true` needs `assessment:write` |
| POST | `/assessments/{id}/publish` | |
| POST | `/assessments/{id}/submissions` | 202, queues evaluation |
| GET | `/submissions`, `/submissions/{id}` | |
| GET | `/evaluations`, `/evaluations/queue` | |
| POST | `/evaluations/{id}/review` | `{"approve":true,"score":1.5,"notes":"…"}` |

The generation response records what was rejected and why:

```json
{
  "validation_report": {
    "total": 8, "accepted": [0,1,2,3,5,6], "rejected": 2,
    "issues": [
      {"index": 4, "prompt": "Which of these…", "problems": ["ambiguous_option","correct_answer_not_in_options"]},
      {"index": 7, "prompt": "How many remote…", "problems": ["duplicate_of_question_0"]}
    ]
  }
}
```

---

## Workflows and webhooks

| Method | Path | Notes |
|---|---|---|
| GET | `/workflows/templates` | The seven built-in templates |
| GET/POST/PATCH | `/workflows` | |
| POST | `/workflows/{id}/trigger` | Idempotent on `idempotency_key` |
| GET | `/executions` | `?status=&workflow_id=` |
| GET | `/executions/{id}` | Steps, payloads, retries, tokens, cost |
| POST | `/executions/{id}/retry` | Only from `failed` or `cancelled` |
| GET | `/executions/{id}/stream` | SSE |
| POST | `/webhooks/n8n/callback` | Signed; replay-protected |
| POST | `/webhooks/events/{org_slug}/{event}` | Signed; emits a domain event |

### Signing

```
X-LearnFlow-Signature: t=<unix seconds>,v1=<hex hmac-sha256>
mac = HMAC_SHA256(WEBHOOK_SIGNING_SECRET, f"{t}.{raw_body}")
```

Verification is constant-time, rejects timestamps outside ±300s, and stores the mac under a
unique constraint — a replayed body returns 401 even inside the freshness window. Sign the
**exact bytes** you send; a re-serialised copy will not match.

```bash
BODY='{"execution_id":"…","status":"succeeded","output":{"ok":true},"steps":[]}'
T=$(date +%s)
SIG=$(printf '%s.%s' "$T" "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SIGNING_SECRET" -hex | cut -d' ' -f2)
curl -X POST localhost:8000/api/v1/webhooks/n8n/callback \
  -H "X-LearnFlow-Signature: t=$T,v1=$SIG" \
  -H 'Content-Type: application/json' -d "$BODY"
```

---

## Analytics, usage and platform

| Method | Path | Notes |
|---|---|---|
| GET | `/analytics/overview` | The fourteen headline metrics; `?exclude_demo=true` drops seeded rows |
| GET | `/analytics/timeseries` | `?metric=ai_requests\|cost\|tokens\|executions\|submissions&days=30` |
| GET | `/analytics/usage` | Cost by model and by operation, plus a daily series. `includes_demo_data` and `demo_requests` report how much of it is seeded; `?exclude_demo=true` removes it |
| GET | `/analytics/workflows` | Per-workflow success rate and average duration |
| GET | `/analytics/inactive-learners` | Consumed by the reminder workflow |
| GET | `/analytics/evaluations` | Review backlog and average confidence |
| GET/POST | `/tickets` | Creating one emits `ticket.created` |
| GET/POST/DELETE | `/api-keys` | The raw key is returned exactly once |
| GET | `/audit-logs` | `?action=&resource_type=` |
| GET | `/jobs`, `/jobs/{id}` | Background job ledger |
| GET | `/integrations` | Live health of every dependency |
| GET | `/health`, `/health/ready` | `/health/ready` returns 503 when degraded |
