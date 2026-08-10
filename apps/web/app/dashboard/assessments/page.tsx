"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Pagination } from "@/components/pagination";
import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Modal,
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type {
  Assessment,
  AssessmentDetail,
  Course,
  DocumentSummary,
  KnowledgeBase,
  Page,
} from "@/lib/types";

const TYPES = ["multiple_choice", "true_false", "short_answer", "scenario"] as const;

export default function AssessmentsPage() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = React.useState(0);
  const [limit, setLimit] = React.useState(25);
  const [open, setOpen] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [knowledgeBaseId, setKnowledgeBaseId] = React.useState("");
  const [courseId, setCourseId] = React.useState("");
  const [documentIds, setDocumentIds] = React.useState<string[]>([]);
  const [questionCount, setQuestionCount] = React.useState(8);
  const [difficulty, setDifficulty] = React.useState("intermediate");
  const [types, setTypes] = React.useState<string[]>(["multiple_choice", "short_answer"]);

  const assessments = useQuery({
    queryKey: ["assessments", offset, limit],
    queryFn: () => api.get<Page<Assessment>>("/api/v1/assessments", { limit, offset }),
  });
  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
    enabled: open,
  });
  const courses = useQuery({
    queryKey: ["courses"],
    queryFn: () => api.get<Page<Course>>("/api/v1/courses", { limit: 50 }),
    enabled: open,
  });
  const documents = useQuery({
    queryKey: ["documents", knowledgeBaseId, "indexed"],
    queryFn: () =>
      api.get<Page<DocumentSummary>>("/api/v1/documents", {
        knowledge_base_id: knowledgeBaseId,
        status: "indexed",
        limit: 50,
      }),
    enabled: open && Boolean(knowledgeBaseId),
  });

  const generate = useMutation({
    mutationFn: () =>
      api.post<AssessmentDetail>("/api/v1/assessments/generate", {
        knowledge_base_id: knowledgeBaseId,
        document_ids: documentIds,
        course_id: courseId || undefined,
        title,
        question_count: questionCount,
        difficulty,
        question_types: types,
      }),
    onSuccess: () => {
      toast.success("Generation queued", {
        description: "Questions are validated for duplicates and ambiguity before they appear.",
      });
      setOpen(false);
      setDocumentIds([]);
      setTitle("");
      void queryClient.invalidateQueries({ queryKey: ["assessments"] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not start generation"),
  });

  return (
    <>
      <PageHeader
        title="Assessments"
        description="Generated questions are rejected when they are duplicated, ambiguous, or unsupported by the source."
        actions={<Button onClick={() => setOpen(true)}>Generate assessment</Button>}
      />

      {assessments.error ? (
        <ErrorState error={assessments.error} retry={() => assessments.refetch()} />
      ) : assessments.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !assessments.data?.items.length ? (
        <EmptyState
          title="No assessments yet"
          description="Generate one from indexed documents."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              Generate assessment
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Assessment</Th>
                <Th>Difficulty</Th>
                <Th className="text-right">Pass mark</Th>
                <Th>Validation</Th>
                <Th>Published</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {assessments.data.items.map((assessment) => {
                const report = assessment.validation_report as {
                  accepted?: number[];
                  rejected?: number;
                };
                return (
                  <tr key={assessment.id} className="hover:bg-muted/50">
                    <Td>
                      <Link
                        href={`/dashboard/assessments/${assessment.id}`}
                        className="font-medium text-primary hover:underline"
                      >
                        {assessment.title}
                      </Link>
                    </Td>
                    <Td className="text-muted-foreground">{assessment.difficulty}</Td>
                    <Td className="text-right tabular">{assessment.passing_score}%</Td>
                    <Td>
                      {report?.accepted ? (
                        <span className="text-2xs text-muted-foreground">
                          {report.accepted.length} accepted
                          {report.rejected ? `, ${report.rejected} rejected` : ""}
                        </span>
                      ) : (
                        <span className="text-2xs text-muted-foreground">pending</span>
                      )}
                    </Td>
                    <Td>
                      {assessment.is_published ? (
                        <Badge tone="success">published</Badge>
                      ) : (
                        <Badge>draft</Badge>
                      )}
                    </Td>
                    <Td className="text-muted-foreground">{dateTime(assessment.created_at)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <Pagination
            total={assessments.data?.total ?? 0}
            limit={limit}
            offset={offset}
            onOffsetChange={setOffset}
            onLimitChange={setLimit}
            label="assessments"
          />
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Generate an assessment">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!documentIds.length) {
              toast.error("Select at least one indexed document");
              return;
            }
            generate.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="a-title">Title</Label>
            <Input
              id="a-title"
              required
              minLength={3}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="a-kb">Knowledge base</Label>
              <Select
                id="a-kb"
                className="w-full"
                required
                value={knowledgeBaseId}
                onChange={(event) => {
                  setKnowledgeBaseId(event.target.value);
                  setDocumentIds([]);
                }}
              >
                <option value="">Select…</option>
                {bases.data?.items.map((kb) => (
                  <option key={kb.id} value={kb.id}>
                    {kb.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="a-course">Course (optional)</Label>
              <Select
                id="a-course"
                className="w-full"
                value={courseId}
                onChange={(event) => setCourseId(event.target.value)}
              >
                <option value="">Standalone</option>
                {courses.data?.items.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.title}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="space-y-1">
            <Label>Source documents</Label>
            <div className="max-h-32 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {!knowledgeBaseId && (
                <p className="text-2xs text-muted-foreground">Select a knowledge base first.</p>
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

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="a-count">Questions</Label>
              <Input
                id="a-count"
                type="number"
                min={1}
                max={30}
                value={questionCount}
                onChange={(event) => setQuestionCount(Number(event.target.value))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="a-difficulty">Difficulty</Label>
              <Select
                id="a-difficulty"
                className="w-full"
                value={difficulty}
                onChange={(event) => setDifficulty(event.target.value)}
              >
                <option value="beginner">Beginner</option>
                <option value="intermediate">Intermediate</option>
                <option value="advanced">Advanced</option>
              </Select>
            </div>
          </div>

          <div className="space-y-1">
            <Label>Question types</Label>
            <div className="flex flex-wrap gap-3">
              {TYPES.map((type) => (
                <label key={type} className="flex items-center gap-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={types.includes(type)}
                    onChange={(event) =>
                      setTypes((previous) =>
                        event.target.checked
                          ? [...previous, type]
                          : previous.filter((value) => value !== type),
                      )
                    }
                  />
                  {type.replace(/_/g, " ")}
                </label>
              ))}
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={generate.isPending} disabled={types.length === 0}>
              Generate
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
