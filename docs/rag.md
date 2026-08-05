# RAG architecture

## Ingestion pipeline

Ten stages, each recorded as a `pipeline_runs` row with status, attempt, duration, details
and error. A failure stops the run, marks the document `failed`, and leaves every prior stage
result intact so the run can resume from the stage that broke.

| Stage | What it does | Fails when |
|---|---|---|
| `upload` | Reads the stored bytes back off disk | The file is missing |
| `validation` | Re-checks the checksum and extension | The file changed on disk |
| `extraction` | PDF (pypdf), DOCX (python-docx), CSV, Markdown/text | No extractable text (a scanned PDF) |
| `cleaning` | NFKC normalise, join hyphen-broken words, strip control chars, collapse whitespace | Nothing usable survives |
| `metadata` | Word count, language guess, section list, title guess | — |
| `chunking` | Sentence-aware packing to a token budget with overlap | The chunker produces nothing |
| `embedding` | Batches of 64 through the embedding provider | Count mismatch, or provider failure after retries |
| `indexing` | Delete this document's old vectors, then upsert; write `document_chunks` | Vector store unavailable |
| `quality` | Near-empty and oversized chunk ratios | More than half the chunks are near-empty |
| `completed` | Mark indexed, recount the knowledge base | — |

Progress is published to Redis at every transition and streamed to the browser over SSE.

## Chunking

Splitting on token count alone cuts mid-sentence and wrecks retrieval; splitting on sentences
alone gives wildly uneven chunks. The chunker packs whole sentences up to a token budget, then
carries a tail of the previous chunk forward as overlap, so a fact spanning a boundary stays
retrievable from both sides.

- Token counting uses `tiktoken` (`cl100k_base`) and degrades to a ~4-chars-per-token estimate
  if the encoding cannot be downloaded — this is budgeting, not billing, so the estimate is fine.
- A single sentence longer than the budget (tables, minified text) is hard-split on words.
- Page numbers and section headings ride along with every chunk, which is what lets a citation
  say "page 4, section Expenses" instead of only naming the file.

Defaults: 700 tokens, 100 overlap. Configurable per knowledge base.

## Vector storage

Isolation is by **collection**, one per organization, named `org_<uuid.hex>`.

The alternative — one shared collection with an `organization_id` metadata filter — fails
open: forget the filter on one query path and you serve another tenant's documents. Collection
naming fails closed: get it wrong and you get nothing back.

`VectorStore` is a small protocol (`upsert`, `query`, `delete`, `count`). The Chroma
implementation runs the sync client in a thread. A Pinecone adapter would map the collection
onto a namespace, Weaviate onto a class — same interface, no caller changes. An in-memory
implementation with identical semantics backs the tests.

## Retrieval

Hybrid, fused with reciprocal rank fusion (k = 60):

1. **Dense** — embed the question, query the organization's collection for `top_k * 2`,
   applying metadata filters for knowledge base and document ids.
2. **Keyword** — an `ILIKE` pass over `document_chunks.content` for up to eight non-stopword
   terms.
3. **Fuse** — `score = sum(1 / (60 + rank))` across both lists.
4. **Threshold** — drop anything under `RETRIEVAL_MIN_SCORE` (default 0.25), keep `top_k`.

Dense search alone misses exact identifiers — policy codes, error codes, SKUs — which is
exactly what people search internal training material for. Keyword-only hits with no dense
payload are dropped rather than re-fetched: a term match with zero semantic similarity is
noise more often than signal.

Retrieval confidence is the mean score of the top three citations.

## Grounding and refusal

If retrieval returns nothing above the threshold, the request **never reaches the model**: the
endpoint returns a refusal, no citations, zero output tokens.

When context is found, the prompt (`rag_answer`, version 3) numbers each passage with its
document name, page and section, and instructs the model to answer only from those passages
with inline `[n]` citations. The response records the citations, the retrieved chunk ids on
the `llm_calls` row, the model, token counts, estimated cost and both latencies.

## Re-indexing and deletion

- `POST /documents/{id}/reindex` re-runs the pipeline; `?from_stage=` resumes partway.
- Indexing deletes the document's existing vectors before upserting, so a re-index never
  leaves orphans behind.
- `DELETE /documents/{id}` removes the vectors, the chunk rows and the stored file.
- Deleting a knowledge base removes every document's vectors first.

## Tuning

| Setting | Default | Effect |
|---|---|---|
| `CHUNK_SIZE_TOKENS` | 700 | Larger keeps context, retrieves less precisely |
| `CHUNK_OVERLAP_TOKENS` | 100 | Higher costs storage, helps boundary-spanning facts |
| `RETRIEVAL_TOP_K` | 6 | More context, more tokens per answer |
| `RETRIEVAL_MIN_SCORE` | 0.25 | Higher refuses more; tuned for the mock embedder, raise it for a real one |
