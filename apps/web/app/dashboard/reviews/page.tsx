"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  Input,
  Label,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Evaluation } from "@/lib/types";

export default function ReviewQueuePage() {
  const queryClient = useQueryClient();
  const [notes, setNotes] = React.useState<Record<string, string>>({});
  const [scores, setScores] = React.useState<Record<string, string>>({});

  const queue = useQuery({
    queryKey: ["evaluations", "queue"],
    queryFn: () => api.get<Evaluation[]>("/api/v1/evaluations/queue"),
  });

  const review = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      api.post<Evaluation>(`/api/v1/evaluations/${id}/review`, {
        approve,
        score: scores[id] ? Number(scores[id]) : undefined,
        notes: notes[id] || undefined,
      }),
    onSuccess: (evaluation) => {
      toast.success(evaluation.status === "approved" ? "Evaluation approved" : "Evaluation rejected");
      void queryClient.invalidateQueries({ queryKey: ["evaluations"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Review failed"),
  });

  return (
    <>
      <PageHeader
        title="Evaluation review"
        description="AI grades below the confidence threshold never reach a learner without a human decision."
      />

      {queue.error ? (
        <ErrorState error={queue.error} retry={() => queue.refetch()} />
      ) : queue.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !queue.data?.length ? (
        <EmptyState
          title="Nothing waiting for review"
          description="Low-confidence evaluations appear here automatically."
        />
      ) : (
        <div className="space-y-4">
          {queue.data.map((evaluation) => (
            <Card key={evaluation.id}>
              <CardHeader className="flex-row items-start justify-between gap-3">
                <div>
                  <CardTitle>Submission {evaluation.submission_id.slice(0, 8)}</CardTitle>
                  <p className="mt-1 text-2xs text-muted-foreground">
                    Graded {dateTime(evaluation.created_at)} by {evaluation.model_used ?? "rules"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone="warning">
                    confidence {evaluation.confidence?.toFixed(2) ?? "—"}
                  </Badge>
                  <Badge tone="info">
                    {evaluation.score ?? 0} / {evaluation.max_score ?? 0}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {evaluation.rationale && (
                  <div>
                    <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                      AI rationale
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">
                      {evaluation.rationale}
                    </p>
                  </div>
                )}

                <div className="grid gap-3 sm:grid-cols-3">
                  <ListBlock title="Strengths" items={evaluation.strengths} />
                  <ListBlock title="Missing concepts" items={evaluation.missing_concepts} />
                  <ListBlock title="Suggested resources" items={evaluation.suggested_resources} />
                </div>

                <div className="grid gap-3 border-t border-border pt-3 sm:grid-cols-[120px_1fr]">
                  <div className="space-y-1">
                    <Label htmlFor={`score-${evaluation.id}`}>Override score</Label>
                    <Input
                      id={`score-${evaluation.id}`}
                      type="number"
                      min={0}
                      max={evaluation.max_score ?? undefined}
                      step="0.5"
                      placeholder={String(evaluation.score ?? "")}
                      value={scores[evaluation.id] ?? ""}
                      onChange={(event) =>
                        setScores({ ...scores, [evaluation.id]: event.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`notes-${evaluation.id}`}>Reviewer notes</Label>
                    <Textarea
                      id={`notes-${evaluation.id}`}
                      className="min-h-[60px]"
                      value={notes[evaluation.id] ?? ""}
                      onChange={(event) =>
                        setNotes({ ...notes, [evaluation.id]: event.target.value })
                      }
                    />
                  </div>
                </div>

                <div className="flex justify-end gap-2">
                  <Button
                    variant="secondary"
                    loading={review.isPending && review.variables?.id === evaluation.id}
                    onClick={() => review.mutate({ id: evaluation.id, approve: false })}
                  >
                    Reject
                  </Button>
                  <Button
                    loading={review.isPending && review.variables?.id === evaluation.id}
                    onClick={() => review.mutate({ id: evaluation.id, approve: true })}
                  >
                    Approve
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-2xs text-muted-foreground">—</p>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {items.map((item) => (
            <li key={item} className="text-2xs text-muted-foreground">
              • {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
