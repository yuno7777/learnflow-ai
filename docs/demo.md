# Demo walkthrough

Fifteen steps, ordered so each leaves visible state for the next. Roughly ten minutes.

## Setup

```bash
cp .env.example .env
docker compose up --build
docker compose exec api python -m learnflow.seed
```

Open http://localhost:3000.

---

### 1. Sign in as an organization administrator

`admin@learnflow.dev` / `LearnFlow!2026` (the login page has a **Use demo credentials**
button). The overview lands on real seeded numbers: ten learners, three courses, three indexed
documents, eighteen workflow runs with genuine failures among them, and thirty days of AI
usage.

**Point out:** every tile is computed from rows. Create a second workspace and it reports
zeros, not placeholder data.

### 2. Upload a training document

**Documents → Upload document.** Any PDF, DOCX, TXT, Markdown or CSV.

**Point out:** rename a `.txt` to `.pdf` and upload it — rejected on magic bytes, not
extension. Upload the same file twice — 409 with the existing document id.

### 3. Watch the ingestion pipeline

Click the document. The stage table fills in live over Server-Sent Events: upload, validation,
extraction, cleaning, metadata, chunking, embedding, indexing, quality, completed — each with
its duration and details.

**Point out:** this is a real audit trail. A failure stops at the offending stage with its
error, and **Retry ingestion** resumes from there rather than re-uploading.

### 4. Inspect what was actually indexed

Scroll to **Stored chunks** — token counts, page numbers, sections and content, so retrieval
quality is inspectable rather than a black box.

### 5. Ask a grounded question

**AI assistant** → "What is the approval code for a remote work request?"

The answer carries numbered citations with document name, page, section and per-chunk score,
plus retrieval confidence, model, provider, token counts, estimated cost, and both retrieval
and model latency.

**Point out:** ask something the material does not cover — "What is the tensile strength of
titanium?" The assistant refuses, returns no citations, and spends zero output tokens. It does
not reach the model at all.

### 6. Generate a course

**Courses → Generate course.** Pick the knowledge base, tick indexed documents, give a
subject, choose four modules.

The row appears as `generating` and refreshes itself; the worker is doing the work.

### 7. Review and publish

Open the course. Modules, lessons, objectives, exercises, and the provenance panel showing
which model and which prompt version produced it.

Edit a lesson in the drawer, regenerate a module, then **Publish**.

**Point out:** generated courses land in `review`, never `published`. Publishing an empty
course is a 409.

### 8. Generate an assessment

**Assessments → Generate assessment.** Eight questions, mixed types.

Open it: each question shows its correct answer, explanation, rubric, source sentence and
confidence. Above them, **Rejected during validation** lists items thrown out for being
duplicated, ambiguous, having an answer not among the options, or lacking a source reference.

**Point out:** untick **answer key** — the same endpoint returns the learner view with the
answers stripped, and a learner role gets a 403 if it asks for them.

### 9. Add a learner

**Learners → Add learner**, optionally enrolling them in the published course.

### 10. Watch the onboarding workflow run

**Executions.** A run appeared without anyone triggering it: creating a learner emitted
`learner.created`, which the onboarding workflow subscribes to.

Open the run. Six steps with durations, the generated welcome message in the output payload,
the token cost attributed to that run, and — if n8n is connected — the n8n execution id. If
n8n is not connected, the first step records `engine: builtin` with the reason.

**Point out:** the live SSE panel while a run is in flight, and **Retry run** on a failed one.

### 11. Take the assessment as a learner

Open the published assessment, **Take as learner**, choose the learner, answer the questions —
get one deliberately wrong or vague.

### 12. Submit for AI evaluation

Submitting returns 202 and queues the evaluation.

### 13. See the evaluation

**Submissions** (or the learner drawer). Multiple-choice and true/false were graded
**deterministically in code**, confidence 1.0, no model call. The written answer was graded
against its rubric with a score, rationale, strengths, missing concepts and suggested
resources.

### 14. Approve it as a reviewer

**Evaluation review.** Anything below the confidence threshold is here, never presented to a
learner as final. Adjust the score, add notes, approve or reject.

**Point out:** try this with an API key instead of a user token — 403. A human must sign off.

### 15. Check analytics and cost

**AI usage and cost:** daily cost and tokens, breakdown by model and by operation, failures per
operation, average latency.

**Analytics:** completion rate, evaluation backlog, average confidence, per-workflow success
rates and durations, and the inactive-learner list the reminder workflow consumes.

**Audit log:** every privileged action from this session, with request ids.

---

### 16. See it from the learner's side

Sign out and back in as `ana.silva@acme.example.com` / `LearnFlow!2026`.

You land on `/learn`, not the dashboard — login routes by role. From here: their enrolled
courses with progress, a lesson reader where ticking a lesson writes real progress back
through the API, an assessment to sit with the answer key stripped out, and Results showing
the feedback once marking is done.

**Point out:** anything still awaiting human review shows as *awaiting review*, never as a
score — the learner is never shown a low-confidence machine grade as if it were final.

**Point out:** open dev tools and try `GET /api/v1/learners` with this token. One row comes
back: themselves. Ask for another learner's id and it is a 404, indistinguishable from an id
that does not exist.

## Extra credit

**Fire a workflow from outside**, the way an HRIS would:

```bash
BODY='{"learner_id":"<uuid>"}'
T=$(date +%s)
SIG=$(printf '%s.%s' "$T" "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SIGNING_SECRET" -hex | cut -d' ' -f2)
curl -X POST localhost:8000/api/v1/webhooks/events/acme-training/learner.created \
  -H "X-LearnFlow-Signature: t=$T,v1=$SIG" \
  -H 'Content-Type: application/json' -d "$BODY"
```

Run it twice with the same body — the second returns 401, replay rejected.

**Show tenant isolation:** register a second organization, take a learner id from the first,
and request it with the second organization's token. 404, indistinguishable from a
nonexistent id.

**Switch providers:** set `LLM_PROVIDER=openai` plus `OPENAI_API_KEY` in `.env`, then
`docker compose up -d --force-recreate api worker`. Nothing else changes; the cost dashboard
starts reporting real spend.

## Recording

```bash
cd apps/web
E2E_RUN=1 npx playwright test --headed
```

The Playwright suite drives steps 1, 5, 9, 10, 14 and 15 and captures a trace on failure.
