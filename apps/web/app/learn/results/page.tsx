"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import * as React from "react";

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Progress,
  Skeleton,
} from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Page, Submission, SubmissionDetail } from "@/lib/types";

/**
 * A learner must never be shown a low-confidence machine grade as if it were final, so
 * anything still awaiting human review is presented as pending rather than as a score.
 */
export default function ResultsPage() {
  const submissions = useQuery({
    queryKey: ["submissions", "mine"],
    queryFn: () => api.get<Page<Submission>>("/api/v1/submissions", { limit: 50 }),
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
  });

  const details = useQueries({
    queries: (submissions.data?.items ?? []).map((s) => ({
      queryKey: ["submission", s.id],
      queryFn: () => api.get<SubmissionDetail>(`/api/v1/submissions/${s.id}`),
    })),
  });

  if (submissions.error) {
    return <ErrorState error={submissions.error} retry={() => submissions.refetch()} />;
  }

  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Results</h1>
      <p className="mt-1 text-xs text-muted-foreground">
        Your submitted assessments and the feedback on them.
      </p>

      <div className="mt-4 space-y-3">
        {submissions.isLoading ? (
          <>
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </>
        ) : !submissions.data?.items.length ? (
          <EmptyState
            title="No submissions yet"
            description="Complete an assessment and your marked result will show up here."
          />
        ) : (
          submissions.data.items.map((submission, index) => {
            const detail = details[index]?.data;
            const evaluation = detail?.evaluation ?? null;
            const awaitingReview =
              !evaluation ||
              evaluation.requires_human_review ||
              evaluation.status === "pending" ||
              evaluation.status === "needs_review";
            const finalised =
              evaluation && ["approved", "ai_completed"].includes(evaluation.status);
            const pct =
              evaluation?.score != null && evaluation.max_score
                ? evaluation.score / evaluation.max_score
                : null;

            return (
              <Card key={submission.id}>
                <CardHeader className="flex-row items-start justify-between gap-3">
                  <div>
                    <CardTitle>Submitted {dateTime(submission.submitted_at)}</CardTitle>
                    <p className="mt-0.5 text-2xs text-muted-foreground">
                      {submission.answers.length} answers
                    </p>
                  </div>
                  {awaitingReview ? (
                    <Badge tone="warning">awaiting review</Badge>
                  ) : (
                    <Badge tone="success">marked</Badge>
                  )}
                </CardHeader>
                <CardContent className="space-y-3">
                  {awaitingReview ? (
                    <p className="text-xs text-muted-foreground">
                      {evaluation
                        ? "A reviewer is checking this before your score is final."
                        : "Marking in progress."}
                    </p>
                  ) : (
                    finalised && (
                      <>
                        <div className="flex items-center justify-between text-sm">
                          <span className="font-medium">
                            {evaluation.score} / {evaluation.max_score}
                          </span>
                          {submission.auto_score !== null && (
                            <span className="tabular text-muted-foreground">
                              {submission.auto_score}%
                            </span>
                          )}
                        </div>
                        {pct !== null && <Progress value={pct} label="score" />}

                        {evaluation.rationale && (
                          <div>
                            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                              Feedback
                            </p>
                            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed">
                              {evaluation.rationale}
                            </p>
                          </div>
                        )}

                        <div className="grid gap-3 sm:grid-cols-3">
                          <Block title="What went well" items={evaluation.strengths} />
                          <Block title="To work on" items={evaluation.missing_concepts} />
                          <Block title="Suggested next" items={evaluation.suggested_resources} />
                        </div>

                        {evaluation.reviewer_notes && (
                          <div className="rounded-md border border-border bg-muted/40 p-3">
                            <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                              Reviewer note
                            </p>
                            <p className="mt-1 text-xs">{evaluation.reviewer_notes}</p>
                          </div>
                        )}
                      </>
                    )
                  )}
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </>
  );
}

function Block({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div>
      <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <ul className="mt-1 space-y-0.5">
        {items.map((item) => (
          <li key={item} className="text-2xs text-muted-foreground">
            • {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
