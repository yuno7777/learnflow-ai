"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
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
  ErrorState,
  Label,
  Modal,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { AssessmentDetail, Learner, Page, SubmissionDetail } from "@/lib/types";

export default function AssessmentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [showAnswers, setShowAnswers] = React.useState(true);
  const [takeOpen, setTakeOpen] = React.useState(false);
  const [learnerId, setLearnerId] = React.useState("");
  const [answers, setAnswers] = React.useState<Record<string, string>>({});

  const assessment = useQuery({
    queryKey: ["assessment", id, showAnswers],
    queryFn: () =>
      api.get<AssessmentDetail>(`/api/v1/assessments/${id}`, { include_answers: showAnswers }),
  });

  const learners = useQuery({
    queryKey: ["learners"],
    queryFn: () => api.get<Page<Learner>>("/api/v1/learners", { limit: 100 }),
    enabled: takeOpen,
  });

  const publish = useMutation({
    mutationFn: () => api.post(`/api/v1/assessments/${id}/publish`),
    onSuccess: () => {
      toast.success("Assessment published");
      void queryClient.invalidateQueries({ queryKey: ["assessment", id] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Publish failed"),
  });

  const submit = useMutation({
    mutationFn: () =>
      api.post<SubmissionDetail>(`/api/v1/assessments/${id}/submissions`, {
        learner_id: learnerId,
        answers: Object.entries(answers).map(([question_id, answer]) => ({ question_id, answer })),
      }),
    onSuccess: () => {
      toast.success("Submitted for AI evaluation", {
        description: "Low-confidence grades appear in the review queue.",
      });
      setTakeOpen(false);
      setAnswers({});
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Submit failed"),
  });

  if (assessment.error) {
    return <ErrorState error={assessment.error} retry={() => assessment.refetch()} />;
  }
  if (assessment.isLoading || !assessment.data) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  const data = assessment.data;
  const report = data.validation_report as {
    total?: number;
    rejected?: number;
    issues?: Array<{ index: number; prompt: string; problems: string[] }>;
  };

  return (
    <>
      <PageHeader
        title={data.title}
        description={`${data.questions.length} questions · pass mark ${data.passing_score}% · ${data.difficulty}`}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/dashboard/assessments" className="text-xs text-primary hover:underline">
              ← All assessments
            </Link>
            <label className="flex items-center gap-1.5 text-2xs">
              <input
                type="checkbox"
                checked={showAnswers}
                onChange={(event) => setShowAnswers(event.target.checked)}
              />
              answer key
            </label>
            {!data.is_published && (
              <Button
                loading={publish.isPending}
                disabled={data.questions.length === 0}
                onClick={() => publish.mutate()}
              >
                Publish
              </Button>
            )}
            {data.is_published && <Button onClick={() => setTakeOpen(true)}>Take as learner</Button>}
          </div>
        }
      />

      {report?.issues && report.issues.length > 0 && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle>Rejected during validation ({report.rejected})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {report.issues.map((issue) => (
              <div key={issue.index} className="text-2xs">
                <p className="text-muted-foreground">{issue.prompt}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {issue.problems.map((problem) => (
                    <Badge key={problem} tone="danger">
                      {problem.replace(/_/g, " ")}
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {data.questions.map((question) => (
          <Card key={question.id}>
            <CardContent className="space-y-2">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm font-medium">
                  {question.position + 1}. {question.prompt}
                </p>
                <div className="flex shrink-0 gap-1">
                  <Badge>{question.type.replace(/_/g, " ")}</Badge>
                  <Badge tone="info">{question.difficulty}</Badge>
                  <Badge>{question.points} pt</Badge>
                </div>
              </div>

              {question.options.length > 0 && (
                <ul className="space-y-1">
                  {question.options.map((option) => (
                    <li
                      key={option}
                      className={
                        showAnswers && option === question.correct_answer
                          ? "text-xs font-medium text-success"
                          : "text-xs text-muted-foreground"
                      }
                    >
                      • {option}
                    </li>
                  ))}
                </ul>
              )}

              {showAnswers && (
                <div className="space-y-1 border-t border-border pt-2 text-2xs text-muted-foreground">
                  {question.options.length === 0 && question.correct_answer && (
                    <p>
                      <span className="font-medium text-foreground">Reference answer: </span>
                      {question.correct_answer}
                    </p>
                  )}
                  {question.explanation && (
                    <p>
                      <span className="font-medium text-foreground">Why: </span>
                      {question.explanation}
                    </p>
                  )}
                  {question.rubric && (
                    <p>
                      <span className="font-medium text-foreground">Rubric: </span>
                      {question.rubric}
                    </p>
                  )}
                  {question.source_reference && (
                    <p>
                      <span className="font-medium text-foreground">Source: </span>
                      {question.source_reference}
                    </p>
                  )}
                  {question.confidence !== null && (
                    <p>
                      <span className="font-medium text-foreground">Confidence: </span>
                      {question.confidence?.toFixed(2)}
                    </p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <Modal open={takeOpen} onClose={() => setTakeOpen(false)} title="Submit answers as a learner">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            submit.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="learner">Learner</Label>
            <Select
              id="learner"
              className="w-full"
              required
              value={learnerId}
              onChange={(event) => setLearnerId(event.target.value)}
            >
              <option value="">Select…</option>
              {learners.data?.items.map((learner) => (
                <option key={learner.id} value={learner.id}>
                  {learner.full_name}
                </option>
              ))}
            </Select>
          </div>

          <div className="max-h-72 space-y-3 overflow-y-auto">
            {data.questions.map((question) => (
              <div key={question.id} className="space-y-1">
                <Label htmlFor={`answer-${question.id}`}>
                  {question.position + 1}. {question.prompt}
                </Label>
                {question.options.length > 0 ? (
                  <Select
                    id={`answer-${question.id}`}
                    className="w-full"
                    value={answers[question.id] ?? ""}
                    onChange={(event) =>
                      setAnswers({ ...answers, [question.id]: event.target.value })
                    }
                  >
                    <option value="">Select…</option>
                    {question.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Textarea
                    id={`answer-${question.id}`}
                    value={answers[question.id] ?? ""}
                    onChange={(event) =>
                      setAnswers({ ...answers, [question.id]: event.target.value })
                    }
                  />
                )}
              </div>
            ))}
          </div>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setTakeOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={submit.isPending} disabled={!learnerId}>
              Submit for evaluation
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
