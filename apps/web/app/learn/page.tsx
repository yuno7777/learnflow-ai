"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
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
import { relativeTime } from "@/lib/format";
import type { Assessment, LearnerDetail, Me, Page } from "@/lib/types";

export default function MyLearningPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/auth/me") });
  const learnerId = me.data?.learner_id;

  const learner = useQuery({
    queryKey: ["learner", learnerId],
    queryFn: () => api.get<LearnerDetail>(`/api/v1/learners/${learnerId}`),
    enabled: Boolean(learnerId),
  });

  // The API already restricts these to published assessments for a learner principal.
  const assessments = useQuery({
    queryKey: ["assessments", "mine"],
    queryFn: () => api.get<Page<Assessment>>("/api/v1/assessments", { limit: 50 }),
  });

  if (me.data && !learnerId) {
    return (
      <EmptyState
        title="This login is not linked to a learner record"
        description="Ask an administrator to grant portal access from the Learners page."
      />
    );
  }

  const enrollments = learner.data?.enrollments ?? [];
  const courseIds = new Set(enrollments.map((e) => e.course_id));
  const myAssessments = (assessments.data?.items ?? []).filter(
    (a) => !a.course_id || courseIds.has(a.course_id),
  );

  return (
    <>
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">
          {learner.data ? `Welcome back, ${learner.data.full_name.split(" ")[0]}` : "My learning"}
        </h1>
        <p className="mt-1 text-xs text-muted-foreground">
          {learner.data?.last_active_at
            ? `Last active ${relativeTime(learner.data.last_active_at)}`
            : "Pick up where you left off."}
        </p>
      </div>

      {learner.error ? (
        <ErrorState error={learner.error} retry={() => learner.refetch()} />
      ) : learner.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : enrollments.length === 0 ? (
        <EmptyState
          title="You are not enrolled in a course yet"
          description="When someone enrols you, it will appear here."
        />
      ) : (
        <div className="space-y-3">
          {enrollments.map((enrollment) => (
            <Card key={enrollment.id}>
              <CardContent className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <Link
                      href={`/learn/courses/${enrollment.course_id}`}
                      className="text-sm font-medium text-primary hover:underline"
                    >
                      {enrollment.course_title ?? "Course"}
                    </Link>
                    <p className="mt-0.5 text-2xs text-muted-foreground">
                      {enrollment.completed_at
                        ? `Completed ${relativeTime(enrollment.completed_at)}`
                        : "In progress"}
                    </p>
                  </div>
                  <Badge tone={enrollment.completed_at ? "success" : "neutral"}>
                    {enrollment.status}
                  </Badge>
                </div>
                <Progress value={enrollment.progress} label={`${enrollment.course_title} progress`} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card className="mt-6">
        <CardHeader>
          <CardTitle>Assessments</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {assessments.isLoading ? (
            <div className="space-y-2 p-4">
              <Skeleton />
              <Skeleton />
            </div>
          ) : myAssessments.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="Nothing to complete right now"
                description="Published assessments for your courses appear here."
              />
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {myAssessments.map((assessment) => (
                <li key={assessment.id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <Link
                      href={`/learn/assessments/${assessment.id}`}
                      className="text-sm font-medium text-primary hover:underline"
                    >
                      {assessment.title}
                    </Link>
                    <p className="text-2xs text-muted-foreground">
                      Pass mark {assessment.passing_score}% · {assessment.difficulty}
                    </p>
                  </div>
                  <Link
                    href={`/learn/assessments/${assessment.id}`}
                    className="shrink-0 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
                  >
                    Start
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
