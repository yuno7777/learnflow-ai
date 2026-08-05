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
  Drawer,
  ErrorState,
  Input,
  Label,
  Skeleton,
  StatusBadge,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { CourseDetail, Lesson } from "@/lib/types";

export default function CourseEditorPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [editing, setEditing] = React.useState<Lesson | null>(null);

  const course = useQuery({
    queryKey: ["course", id],
    queryFn: () => api.get<CourseDetail>(`/api/v1/courses/${id}`),
    refetchInterval: (query) => (query.state.data?.status === "generating" ? 3000 : false),
  });

  const publish = useMutation({
    mutationFn: () => api.post<CourseDetail>(`/api/v1/courses/${id}/publish`),
    onSuccess: () => {
      toast.success("Course published");
      void queryClient.invalidateQueries({ queryKey: ["course", id] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Publish failed"),
  });

  const regenerate = useMutation({
    mutationFn: (moduleId: string) =>
      api.post(`/api/v1/courses/${id}/modules/${moduleId}/regenerate`, { instructions: null }),
    onSuccess: () =>
      toast.success("Module regeneration queued", {
        description: "Refresh in a moment to see the rewritten module.",
      }),
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Regenerate failed"),
  });

  const saveLesson = useMutation({
    mutationFn: (lesson: Lesson) =>
      api.patch<Lesson>(`/api/v1/courses/${id}/lessons/${lesson.id}`, {
        title: lesson.title,
        summary: lesson.summary,
        content: lesson.content,
        exercise: lesson.exercise,
      }),
    onSuccess: () => {
      toast.success("Lesson saved");
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["course", id] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Save failed"),
  });

  if (course.error) return <ErrorState error={course.error} retry={() => course.refetch()} />;
  if (course.isLoading || !course.data) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  const data = course.data;

  return (
    <>
      <PageHeader
        title={data.title}
        description={data.description ?? undefined}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/dashboard/courses" className="text-xs text-primary hover:underline">
              ← All courses
            </Link>
            <StatusBadge status={data.status} />
            {data.status !== "published" && (
              <Button
                loading={publish.isPending}
                disabled={data.status === "generating" || data.modules.length === 0}
                onClick={() => publish.mutate()}
              >
                Publish
              </Button>
            )}
          </div>
        }
      />

      {data.status === "generating" && (
        <Card className="mb-4">
          <CardContent className="flex items-center gap-3">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <p className="text-sm">
              The worker is generating this course. This page refreshes itself.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-4">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Modules and lessons</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {data.modules.length === 0 && (
              <p className="text-xs text-muted-foreground">No modules yet.</p>
            )}
            {data.modules.map((module) => (
              <div key={module.id} className="rounded-lg border border-border">
                <div className="flex items-start justify-between gap-3 border-b border-border px-3 py-2">
                  <div>
                    <p className="text-sm font-medium">
                      {module.position + 1}. {module.title}
                    </p>
                    {module.summary && (
                      <p className="mt-0.5 text-2xs text-muted-foreground">{module.summary}</p>
                    )}
                    <div className="mt-1 flex flex-wrap gap-1">
                      {module.learning_objectives.map((objective) => (
                        <Badge key={objective}>{objective}</Badge>
                      ))}
                    </div>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={regenerate.isPending && regenerate.variables === module.id}
                    onClick={() => regenerate.mutate(module.id)}
                  >
                    Regenerate
                  </Button>
                </div>
                <ul className="divide-y divide-border">
                  {module.lessons.map((lesson) => (
                    <li key={lesson.id} className="flex items-center justify-between gap-3 px-3 py-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm">{lesson.title}</p>
                        {lesson.summary && (
                          <p className="truncate text-2xs text-muted-foreground">{lesson.summary}</p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        {lesson.estimated_minutes && (
                          <span className="text-2xs text-muted-foreground">
                            {lesson.estimated_minutes} min
                          </span>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => setEditing(lesson)}>
                          Edit
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Provenance</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              <Row label="Model" value={data.model_used ?? "—"} />
              <Row label="Prompt version" value={data.prompt_version ?? "—"} />
              <Row label="Enrolments" value={data.enrollment_count} />
              <Row label="Created" value={dateTime(data.created_at)} />
              <Row label="Published" value={dateTime(data.published_at)} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Learning objectives</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1">
                {data.learning_objectives.map((objective) => (
                  <li key={objective} className="text-2xs text-muted-foreground">
                    • {objective}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>

      <Drawer
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        title={editing ? `Edit: ${editing.title}` : ""}
      >
        {editing && (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              saveLesson.mutate(editing);
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="lesson-title">Title</Label>
              <Input
                id="lesson-title"
                value={editing.title}
                onChange={(event) => setEditing({ ...editing, title: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="lesson-summary">Summary</Label>
              <Textarea
                id="lesson-summary"
                value={editing.summary ?? ""}
                onChange={(event) => setEditing({ ...editing, summary: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="lesson-content">Content (markdown)</Label>
              <Textarea
                id="lesson-content"
                className="min-h-[220px] font-mono text-xs"
                value={editing.content ?? ""}
                onChange={(event) => setEditing({ ...editing, content: event.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="lesson-exercise">Exercise</Label>
              <Textarea
                id="lesson-exercise"
                value={editing.exercise ?? ""}
                onChange={(event) => setEditing({ ...editing, exercise: event.target.value })}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button type="submit" loading={saveLesson.isPending}>
                Save lesson
              </Button>
            </div>
          </form>
        )}
      </Drawer>
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular font-medium">{value}</span>
    </div>
  );
}
