"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Label,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { AssessmentDetail, Me, SubmissionDetail } from "@/lib/types";

export default function TakeAssessmentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [answers, setAnswers] = React.useState<Record<string, string>>({});

  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/auth/me") });
  const learnerId = me.data?.learner_id;

  // The API strips the answer key for a learner principal, so this is the honest view.
  const assessment = useQuery({
    queryKey: ["assessment", id, "learner"],
    queryFn: () => api.get<AssessmentDetail>(`/api/v1/assessments/${id}`),
  });

  const submit = useMutation({
    mutationFn: () =>
      api.post<SubmissionDetail>(`/api/v1/assessments/${id}/submissions`, {
        learner_id: learnerId,
        answers: Object.entries(answers).map(([question_id, answer]) => ({
          question_id,
          answer,
        })),
      }),
    onSuccess: () => {
      toast.success("Submitted", {
        description: "Your answers are being marked. Results appear under Results.",
      });
      router.push("/learn/results");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Submit failed"),
  });

  if (assessment.error) {
    return <ErrorState error={assessment.error} retry={() => assessment.refetch()} />;
  }
  if (assessment.isLoading || !assessment.data) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  const data = assessment.data;
  const answered = data.questions.filter((q) => (answers[q.id] ?? "").trim().length > 0).length;
  const allAnswered = answered === data.questions.length;

  return (
    <>
      <Link href="/learn" className="text-xs text-primary hover:underline">
        ← My learning
      </Link>
      <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{data.title}</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {data.questions.length} questions · pass mark {data.passing_score}% · {data.difficulty}
          </p>
        </div>
        <Badge tone={allAnswered ? "success" : "neutral"}>
          {answered}/{data.questions.length} answered
        </Badge>
      </div>

      <form
        className="mt-4 space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit.mutate();
        }}
      >
        {data.questions.map((question) => (
          <Card key={question.id}>
            <CardHeader>
              <CardTitle>
                {question.position + 1}. {question.prompt}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {question.options.length > 0 ? (
                <fieldset className="space-y-1.5">
                  <legend className="sr-only">{question.prompt}</legend>
                  {question.options.map((option) => (
                    <label
                      key={option}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-2 text-sm hover:bg-muted/50"
                    >
                      <input
                        type="radio"
                        name={question.id}
                        value={option}
                        checked={answers[question.id] === option}
                        onChange={() => setAnswers({ ...answers, [question.id]: option })}
                      />
                      {option}
                    </label>
                  ))}
                </fieldset>
              ) : (
                <div className="space-y-1">
                  <Label htmlFor={`a-${question.id}`}>Your answer</Label>
                  <Textarea
                    id={`a-${question.id}`}
                    className="min-h-[100px]"
                    value={answers[question.id] ?? ""}
                    onChange={(event) =>
                      setAnswers({ ...answers, [question.id]: event.target.value })
                    }
                  />
                  <p className="text-2xs text-muted-foreground">
                    Written answers are marked against a rubric, and a person reviews anything
                    the marker is unsure about.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        ))}

        <div className="sticky bottom-0 flex items-center justify-between gap-3 border-t border-border bg-background py-3">
          <p className="text-2xs text-muted-foreground">
            {allAnswered ? "Ready to submit." : `${data.questions.length - answered} left.`}
          </p>
          <Button type="submit" loading={submit.isPending} disabled={answered === 0 || !learnerId}>
            Submit answers
          </Button>
        </div>
      </form>
    </>
  );
}
