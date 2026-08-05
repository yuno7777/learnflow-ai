"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Button,
  Card,
  CardContent,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Modal,
  Progress,
  Select,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime, relativeTime } from "@/lib/format";
import type { Course, Learner, LearnerDetail, Page } from "@/lib/types";

export default function LearnersPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [selected, setSelected] = React.useState<string | null>(null);
  const [form, setForm] = React.useState({ email: "", full_name: "", course_id: "" });

  const learners = useQuery({
    queryKey: ["learners", search],
    queryFn: () =>
      api.get<Page<Learner>>("/api/v1/learners", {
        limit: 50,
        search: search.length > 1 ? search : undefined,
      }),
  });

  const courses = useQuery({
    queryKey: ["courses", "published"],
    queryFn: () => api.get<Page<Course>>("/api/v1/courses", { limit: 50 }),
  });

  const detail = useQuery({
    queryKey: ["learner", selected],
    queryFn: () => api.get<LearnerDetail>(`/api/v1/learners/${selected}`),
    enabled: Boolean(selected),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<LearnerDetail>("/api/v1/learners", {
        email: form.email,
        full_name: form.full_name,
        course_id: form.course_id || undefined,
      }),
    onSuccess: () => {
      toast.success("Learner added", {
        description: "The onboarding workflow has been triggered.",
      });
      setOpen(false);
      setForm({ email: "", full_name: "", course_id: "" });
      void queryClient.invalidateQueries({ queryKey: ["learners"] });
      void queryClient.invalidateQueries({ queryKey: ["executions"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not add learner"),
  });

  return (
    <>
      <PageHeader
        title="Learners"
        description="Adding a learner emits learner.created, which fires the onboarding workflow."
        actions={
          <div className="flex items-center gap-2">
            <Input
              placeholder="Search name or email"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="w-56"
              aria-label="Search learners"
            />
            <Button onClick={() => setOpen(true)}>Add learner</Button>
          </div>
        }
      />

      {learners.error ? (
        <ErrorState error={learners.error} retry={() => learners.refetch()} />
      ) : learners.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !learners.data?.items.length ? (
        <EmptyState
          title="No learners yet"
          description="Add one to see the onboarding workflow run end to end."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              Add learner
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Learner</Th>
                <Th>Status</Th>
                <Th>Last active</Th>
                <Th className="text-right">Reminders</Th>
                <Th>External ref</Th>
                <Th>Added</Th>
              </tr>
            </thead>
            <tbody>
              {learners.data.items.map((learner) => (
                <tr
                  key={learner.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelected(learner.id)}
                >
                  <Td>
                    <p className="font-medium">{learner.full_name}</p>
                    <p className="text-2xs text-muted-foreground">{learner.email}</p>
                  </Td>
                  <Td>
                    <StatusBadge status={learner.status} />
                  </Td>
                  <Td className="text-muted-foreground">{relativeTime(learner.last_active_at)}</Td>
                  <Td className="text-right tabular">{learner.reminders_sent}</Td>
                  <Td className="text-2xs text-muted-foreground">{learner.external_ref ?? "—"}</Td>
                  <Td className="text-muted-foreground">{dateTime(learner.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={detail.data?.full_name ?? "Learner"}
      >
        {detail.isLoading || !detail.data ? (
          <div className="space-y-2">
            <Skeleton />
            <Skeleton />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1 text-xs">
              <p className="text-muted-foreground">{detail.data.email}</p>
              <p className="text-muted-foreground">
                Last active {relativeTime(detail.data.last_active_at)} · {detail.data.reminders_sent}{" "}
                reminders sent
              </p>
            </div>

            <div>
              <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                Enrolments
              </p>
              {detail.data.enrollments.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">Not enrolled in any course.</p>
              ) : (
                <ul className="mt-2 space-y-3">
                  {detail.data.enrollments.map((enrollment) => (
                    <li key={enrollment.id}>
                      <div className="flex items-center justify-between text-xs">
                        <span>{enrollment.course_title}</span>
                        <StatusBadge status={enrollment.status} />
                      </div>
                      <div className="mt-1">
                        <Progress value={enrollment.progress} label="course progress" />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {Array.isArray(detail.data.profile?.onboarding_checklist) && (
              <div>
                <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Onboarding checklist
                </p>
                <ul className="mt-2 space-y-1">
                  {(
                    detail.data.profile.onboarding_checklist as Array<{
                      task: string;
                      done: boolean;
                    }>
                  ).map((item) => (
                    <li key={item.task} className="text-xs text-muted-foreground">
                      {item.done ? "☑" : "☐"} {item.task}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                await api.post(`/api/v1/learners/${detail.data.id}/activity`);
                toast.success("Activity recorded");
                void queryClient.invalidateQueries({ queryKey: ["learner", selected] });
                void queryClient.invalidateQueries({ queryKey: ["learners"] });
              }}
            >
              Record activity
            </Button>
          </div>
        )}
      </Drawer>

      <Modal open={open} onClose={() => setOpen(false)} title="Add learner">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="learner-name">Full name</Label>
            <Input
              id="learner-name"
              required
              value={form.full_name}
              onChange={(event) => setForm({ ...form, full_name: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="learner-email">Email</Label>
            <Input
              id="learner-email"
              type="email"
              required
              value={form.email}
              onChange={(event) => setForm({ ...form, email: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="learner-course">Enrol in course (optional)</Label>
            <Select
              id="learner-course"
              className="w-full"
              value={form.course_id}
              onChange={(event) => setForm({ ...form, course_id: event.target.value })}
            >
              <option value="">No course</option>
              {courses.data?.items.map((course) => (
                <option key={course.id} value={course.id}>
                  {course.title}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Add learner
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
