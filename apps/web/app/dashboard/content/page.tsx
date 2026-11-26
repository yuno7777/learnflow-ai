"use client";

import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Label,
  Progress,
  Select,
  Skeleton,
  StatusBadge,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { currency } from "@/lib/format";
import type { DocumentSummary, Job, KnowledgeBase, Page, TransformJob } from "@/lib/types";

const LABELS: Record<string, string> = {
  short_summary: "Short summary",
  detailed_notes: "Detailed notes",
  flashcards: "Flashcards",
  faq: "FAQ",
  quiz: "Quiz",
  presentation_outline: "Presentation outline",
  social_post: "Social learning post",
  email_announcement: "Email announcement",
};

interface TransformResult {
  output_type?: string;
  title?: string;
  body?: string;
  bullet_points?: string[];
  model?: string;
  estimated_cost_usd?: number;
}

export default function ContentStudioPage() {
  const [selectedKnowledgeBaseId, setKnowledgeBaseId] = React.useState("");
  const [documentIds, setDocumentIds] = React.useState<string[]>([]);
  const [outputTypes, setOutputTypes] = React.useState<string[]>(["short_summary", "faq"]);
  const [instructions, setInstructions] = React.useState("");
  const [queued, setQueued] = React.useState<TransformJob[]>([]);

  const types = useQuery({
    queryKey: ["content", "output-types"],
    queryFn: () => api.get<string[]>("/api/v1/content/output-types"),
  });
  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
  });

  const knowledgeBaseId = selectedKnowledgeBaseId || bases.data?.items[0]?.id || "";

  const documents = useQuery({
    queryKey: ["documents", knowledgeBaseId, "indexed"],
    queryFn: () =>
      api.get<Page<DocumentSummary>>("/api/v1/documents", {
        knowledge_base_id: knowledgeBaseId,
        status: "indexed",
        limit: 50,
      }),
    enabled: Boolean(knowledgeBaseId),
  });

  // Each output type is its own background job, so each gets its own progress row.
  const jobs = useQueries({
    queries: queued.map((item) => ({
      queryKey: ["job", item.job_id],
      queryFn: () => api.get<Job>(`/api/v1/jobs/${item.job_id}`),
      refetchInterval: (query: { state: { data?: Job } }) =>
        query.state.data && ["succeeded", "failed"].includes(query.state.data.status)
          ? false
          : 1500,
    refetchIntervalInBackground: true,
    })),
  });

  const transform = useMutation({
    mutationFn: () =>
      api.post<{ jobs: TransformJob[] }>("/api/v1/content/transform", {
        document_ids: documentIds,
        output_types: outputTypes,
        instructions,
      }),
    onSuccess: (data) => {
      setQueued(data.jobs);
      toast.success(`${data.jobs.length} transformation${data.jobs.length > 1 ? "s" : ""} queued`, {
        description: "Each output runs as its own background job.",
      });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not start transformation"),
  });

  const noBases = !bases.isLoading && !bases.data?.items.length;

  return (
    <>
      <PageHeader
        title="Content studio"
        description="Turn indexed training material into summaries, notes, flashcards, FAQs, quizzes, outlines and announcements. Each output is generated asynchronously as its own workflow step."
      />

      {noBases ? (
        <EmptyState
          title="Index a document first"
          description="Content is always generated from material already in a knowledge base."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-1">
            <CardHeader>
              <CardTitle>Source and outputs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1">
                <Label htmlFor="kb">Knowledge base</Label>
                <Select
                  id="kb"
                  className="w-full"
                  value={knowledgeBaseId}
                  onChange={(event) => {
                    setKnowledgeBaseId(event.target.value);
                    setDocumentIds([]);
                  }}
                >
                  {bases.data?.items.map((kb) => (
                    <option key={kb.id} value={kb.id}>
                      {kb.name}
                    </option>
                  ))}
                </Select>
              </div>

              <div className="space-y-1">
                <Label>Documents</Label>
                <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                  {documents.isLoading && <Skeleton className="h-4" />}
                  {!documents.isLoading && !documents.data?.items.length && (
                    <p className="text-2xs text-muted-foreground">
                      No indexed documents in this knowledge base.
                    </p>
                  )}
                  {documents.data?.items.map((document) => (
                    <label key={document.id} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={documentIds.includes(document.id)}
                        onChange={(event) =>
                          setDocumentIds((previous) =>
                            event.target.checked
                              ? [...previous, document.id]
                              : previous.filter((id) => id !== document.id),
                          )
                        }
                      />
                      {document.filename}
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-1">
                <Label>Outputs</Label>
                <div className="grid grid-cols-1 gap-1">
                  {(types.data ?? []).map((type) => (
                    <label key={type} className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={outputTypes.includes(type)}
                        onChange={(event) =>
                          setOutputTypes((previous) =>
                            event.target.checked
                              ? [...previous, type]
                              : previous.filter((value) => value !== type),
                          )
                        }
                      />
                      {LABELS[type] ?? type.replace(/_/g, " ")}
                    </label>
                  ))}
                </div>
              </div>

              <div className="space-y-1">
                <Label htmlFor="instructions">Extra instructions (optional)</Label>
                <Textarea
                  id="instructions"
                  className="min-h-[60px]"
                  placeholder="Audience, tone, length…"
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                />
              </div>

              <Button
                className="w-full"
                loading={transform.isPending}
                disabled={!documentIds.length || !outputTypes.length}
                onClick={() => transform.mutate()}
              >
                Generate {outputTypes.length || ""} output
                {outputTypes.length === 1 ? "" : "s"}
              </Button>
              {!documentIds.length && (
                <p className="text-2xs text-muted-foreground">Select at least one document.</p>
              )}
            </CardContent>
          </Card>

          <div className="space-y-4 lg:col-span-2">
            {queued.length === 0 ? (
              <EmptyState
                title="Nothing generated yet"
                description="Pick a document and one or more outputs, then generate. Results appear here as each job finishes."
              />
            ) : (
              queued.map((item, index) => {
                const query = jobs[index];
                const job = query?.data;
                const result = (job?.result ?? {}) as TransformResult;
                return (
                  <Card key={item.job_id}>
                    <CardHeader className="flex-row items-center justify-between">
                      <CardTitle>{LABELS[item.output_type] ?? item.output_type}</CardTitle>
                      <div className="flex items-center gap-2">
                        {result.model && <Badge>{result.model}</Badge>}
                        {result.estimated_cost_usd !== undefined && (
                          <Badge tone="info">{currency(result.estimated_cost_usd, 6)}</Badge>
                        )}
                        <StatusBadge status={job?.status ?? "queued"} />
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {query?.error ? (
                        <ErrorState error={query.error} retry={() => query.refetch()} />
                      ) : !job || ["queued", "running"].includes(job.status) ? (
                        <>
                          <Progress value={job?.progress ?? 0} label={item.output_type} />
                          <p className="text-2xs text-muted-foreground">
                            {job?.message ?? "Waiting for the worker…"}
                          </p>
                        </>
                      ) : job.status === "failed" ? (
                        <div className="rounded-md border border-danger/30 bg-danger/5 p-3">
                          <p className="text-xs font-medium text-danger">Generation failed</p>
                          <p className="mt-1 text-2xs text-muted-foreground">{job.error}</p>
                        </div>
                      ) : (
                        <>
                          {result.title && <p className="text-sm font-medium">{result.title}</p>}
                          {result.body && (
                            <p className="whitespace-pre-wrap text-xs leading-relaxed">
                              {result.body}
                            </p>
                          )}
                          {result.bullet_points && result.bullet_points.length > 0 && (
                            <ul className="space-y-1">
                              {result.bullet_points.map((point) => (
                                <li key={point} className="text-xs text-muted-foreground">
                                  • {point}
                                </li>
                              ))}
                            </ul>
                          )}
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={async () => {
                              await navigator.clipboard.writeText(
                                [result.title, result.body, ...(result.bullet_points ?? [])]
                                  .filter(Boolean)
                                  .join("\n\n"),
                              );
                              toast.success("Copied to clipboard");
                            }}
                          >
                            Copy
                          </Button>
                        </>
                      )}
                    </CardContent>
                  </Card>
                );
              })
            )}
          </div>
        </div>
      )}
    </>
  );
}
