import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = { title: "Features" };

const SECTIONS = [
  {
    heading: "Document ingestion pipeline",
    points: [
      "Ten recorded stages: upload, validation, extraction, cleaning, metadata, chunking, embedding, indexing, quality, completion.",
      "Every stage attempt is a row you can inspect, with duration, details and error text.",
      "A failed run is resumable from the stage that broke — no re-upload.",
      "Uploads are checked against their magic bytes, not just their extension.",
      "Progress streams to the browser over Server-Sent Events.",
    ],
  },
  {
    heading: "Retrieval",
    points: [
      "Hybrid: dense vector search fused with a keyword pass via reciprocal rank fusion, so exact identifiers (policy codes, SKUs) are still findable.",
      "Similarity threshold enforced before an answer is attempted.",
      "Citations carry document name, page, section and per-chunk score.",
      "One Chroma collection per organization — isolation by collection, not by a filter that can be forgotten.",
    ],
  },
  {
    heading: "AI layer",
    points: [
      "Configurable providers: OpenAI, Azure OpenAI, any OpenAI-compatible endpoint, local Ollama, or a deterministic offline mock.",
      "Structured output validated against Pydantic schemas with one repair attempt before failing.",
      "Automatic retries with backoff, rate-limit handling and provider fallback.",
      "Versioned prompts; the version is written onto every call record.",
      "Token counts, latency, retries and estimated cost recorded per call.",
    ],
  },
  {
    heading: "Automation",
    points: [
      "Five exportable n8n workflows in the repository, driven by HMAC-signed webhooks.",
      "Replay protection: the signature is stored under a unique constraint, so a replayed body is rejected.",
      "Signed callbacks close out executions and attach the steps n8n ran.",
      "Built-in runners execute the same steps when n8n is unavailable, so the platform degrades instead of stalling.",
      "Manual retry, retry counts and full input/output payloads on every run.",
    ],
  },
  {
    heading: "Governance",
    points: [
      "Six roles with a permission map enforced server-side on every route.",
      "API keys stored hashed, shown once, revocable.",
      "Audit log for every privileged action, with request IDs.",
      "Secret-looking values are redacted before anything reaches a log sink.",
    ],
  },
];

export default function FeaturesPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <Link href="/" className="text-xs text-muted-foreground hover:text-foreground">
        ← Back
      </Link>
      <h1 className="mt-4 text-2xl font-semibold tracking-tight">What LearnFlow actually does</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        No feature below is a placeholder — each is exercised by the test suite.
      </p>

      <div className="mt-10 space-y-10">
        {SECTIONS.map((section) => (
          <section key={section.heading}>
            <h2 className="text-sm font-semibold">{section.heading}</h2>
            <ul className="mt-3 space-y-2">
              {section.points.map((point) => (
                <li key={point} className="flex gap-2 text-xs leading-relaxed text-muted-foreground">
                  <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary" />
                  {point}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
