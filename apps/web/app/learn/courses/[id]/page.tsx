"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Progress,
  Skeleton,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { CourseDetail, Enrollment, LearnerDetail, Lesson, Me } from "@/lib/types";

export default function LessonReaderPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [openLesson, setOpenLesson] = React.useState<string | null>(null);

  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/auth/me") });
  const learnerId = me.data?.learner_id;

  const course = useQuery({
    queryKey: ["course", id],
    queryFn: () => api.get<CourseDetail>(`/api/v1/courses/${id}`),
  });
  const learner = useQuery({
    queryKey: ["learner", learnerId],
    queryFn: () => api.get<LearnerDetail>(`/api/v1/learners/${learnerId}`),
    enabled: Boolean(learnerId),
  });

  const enrollment = learner.data?.enrollments.find((e) => e.course_id === id);

  // completed_lesson_ids rides along on the enrolment the learner detail endpoint returns.
  const completed = React.useMemo(
    () => new Set(enrollment?.completed_lesson_ids ?? []),
    [enrollment],
  );

  const mark = useMutation({
    mutationFn: ({ lessonId, done }: { lessonId: string; done: boolean }) =>
      api.post<Enrollment>(
        `/api/v1/learners/${learnerId}/progress`,
        { lesson_id: lessonId, completed: done },
        { course_id: id },
      ),
    onSuccess: (updated) => {
      if (updated.completed_at) toast.success("Course complete. Nice work.");
      void queryClient.invalidateQueries({ queryKey: ["learner", learnerId] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not save progress"),
  });

  if (course.error) return <ErrorState error={course.error} retry={() => course.refetch()} />;
  if (course.isLoading || !course.data) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  const data = course.data;
  const totalLessons = data.modules.reduce((n, m) => n + m.lessons.length, 0);

  return (
    <>
      <Link href="/learn" className="text-xs text-primary hover:underline">
        ← My learning
      </Link>
      <h1 className="mt-3 text-xl font-semibold tracking-tight">{data.title}</h1>
      {data.description && (
        <p className="mt-1 text-sm text-muted-foreground">{data.description}</p>
      )}

      <Card className="mt-4">
        <CardContent className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">
              {completed.size} of {totalLessons} lessons complete
            </span>
            {enrollment?.completed_at && <Badge tone="success">completed</Badge>}
          </div>
          <Progress value={enrollment?.progress ?? 0} label="course progress" />
        </CardContent>
      </Card>

      <div className="mt-4 space-y-4">
        {data.modules.map((module) => (
          <Card key={module.id}>
            <CardHeader>
              <CardTitle>
                {module.position + 1}. {module.title}
              </CardTitle>
              {module.summary && (
                <p className="text-2xs text-muted-foreground">{module.summary}</p>
              )}
            </CardHeader>
            <CardContent className="p-0">
              <ul className="divide-y divide-border">
                {module.lessons.map((lesson: Lesson) => {
                  const done = completed.has(lesson.id);
                  const open = openLesson === lesson.id;
                  return (
                    <li key={lesson.id} className="px-4 py-3">
                      <div className="flex items-start gap-3">
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={done}
                          disabled={mark.isPending || !learnerId}
                          aria-label={`Mark "${lesson.title}" complete`}
                          onChange={(event) =>
                            mark.mutate({ lessonId: lesson.id, done: event.target.checked })
                          }
                        />
                        <div className="min-w-0 flex-1">
                          <button
                            className="text-left text-sm font-medium hover:text-primary"
                            onClick={() => setOpenLesson(open ? null : lesson.id)}
                            aria-expanded={open}
                          >
                            {lesson.title}
                          </button>
                          {lesson.summary && (
                            <p className="mt-0.5 text-2xs text-muted-foreground">
                              {lesson.summary}
                            </p>
                          )}
                          {open && (
                            <div className="mt-3 space-y-3 border-l-2 border-border pl-3">
                              {lesson.content && (
                                <p className="whitespace-pre-wrap text-xs leading-relaxed">
                                  {lesson.content}
                                </p>
                              )}
                              {lesson.exercise && (
                                <div className="rounded-md bg-muted/50 p-3">
                                  <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                                    Try it
                                  </p>
                                  <p className="mt-1 text-xs">{lesson.exercise}</p>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                        {lesson.estimated_minutes && (
                          <span className="shrink-0 text-2xs text-muted-foreground">
                            {lesson.estimated_minutes} min
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}
