import Link from "next/link";

const CAPABILITIES = [
  {
    title: "Knowledge base and RAG",
    body: "Upload PDF, DOCX, TXT, Markdown or CSV. A ten-stage pipeline extracts, cleans, chunks, embeds and indexes it. Answers come back with source documents, page numbers and a retrieval confidence — and the assistant refuses rather than guesses when the material does not cover the question.",
  },
  {
    title: "Course and assessment generation",
    body: "Generate a full course structure or a mixed-format assessment from selected documents. Output is validated against a strict schema, then checked for duplicate, ambiguous and unsupported questions before an instructor ever sees it.",
  },
  {
    title: "AI evaluation with human sign-off",
    body: "Objective questions are graded deterministically in code. Free-text answers are graded against a rubric, and anything below the confidence threshold is routed to a reviewer instead of being presented as final.",
  },
  {
    title: "Automation through n8n",
    body: "Learner onboarding, inactivity reminders, ticket triage and document notifications run as n8n workflows driven by signed webhooks, with execution history, retries and live status streamed back into the dashboard.",
  },
  {
    title: "Cost and reliability observability",
    body: "Every model call records provider, model, prompt version, token counts, latency, retries and estimated cost — broken down per model and per operation, with workflow success rates alongside.",
  },
  {
    title: "Multi-tenant by construction",
    body: "Tenant isolation is enforced in the data layer, not the UI: the repository refuses to build a query without an organization filter, and each tenant's vectors live in their own collection.",
  },
];

const STACK = [
  "FastAPI",
  "Next.js 15",
  "PostgreSQL",
  "Redis + ARQ",
  "ChromaDB",
  "n8n",
  "Docker",
  "Kubernetes",
];

export default function LandingPage() {
  return (
    <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-6">
      <header className="flex items-center justify-between py-6">
        <span className="text-sm font-semibold tracking-tight">LearnFlow AI</span>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/features" className="text-muted-foreground hover:text-foreground">
            Features
          </Link>
          <Link href="/login" className="text-muted-foreground hover:text-foreground">
            Sign in
          </Link>
          <Link
            href="/register"
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Create workspace
          </Link>
        </nav>
      </header>

      <main id="main" className="flex-1 py-12">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
          Learning operations platform
        </p>
        <h1 className="mt-3 max-w-2xl text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
          Automate the operational work around training, without guessing.
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          LearnFlow turns your existing training material into a searchable knowledge base,
          generates reviewable courses and assessments from it, grades written answers under
          human supervision, and runs the follow-up — onboarding, reminders, ticket triage —
          as auditable workflows. Every AI response carries its sources, its token cost and
          its confidence.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/register"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Create a workspace
          </Link>
          <Link
            href="/login"
            className="rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-muted"
          >
            Sign in to the demo
          </Link>
        </div>

        <dl className="mt-14 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2">
          {CAPABILITIES.map((item) => (
            <div key={item.title} className="bg-card p-5">
              <dt className="text-sm font-semibold">{item.title}</dt>
              <dd className="mt-2 text-xs leading-relaxed text-muted-foreground">{item.body}</dd>
            </div>
          ))}
        </dl>

        <section className="mt-12">
          <h2 className="text-sm font-semibold">Built on</h2>
          <ul className="mt-3 flex flex-wrap gap-2">
            {STACK.map((item) => (
              <li
                key={item}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground"
              >
                {item}
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="border-t border-border py-6 text-xs text-muted-foreground">
        LearnFlow AI · API documentation at{" "}
        <code className="rounded bg-muted px-1 py-0.5">/docs</code> when the backend is running.
      </footer>
    </div>
  );
}
