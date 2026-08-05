"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
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
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { CourseDetail, Course, DocumentSummary, KnowledgeBase, Page } from "@/lib/types";

export default function CoursesPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [subject, setSubject] = React.useState("");
  const [moduleCount, setModuleCount] = React.useState(4);
  const [audience, setAudience] = React.useState("new employees");
  const [knowledgeBaseId, setKnowledgeBaseId] = React.useState("");
  const [documentIds, setDocumentIds] = React.useState<string[]>([]);

  const courses = useQuery({
    queryKey: ["courses"],
    queryFn: () => api.get<Page<Course>>("/api/v1/courses", { limit: 50 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((c) => c.status === "generating") ? 3000 : false,
  });

  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
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
      api.post<CourseDetail>("/api/v1/courses/generate", {
        knowledge_base_id: knowledgeBaseId,
        document_ids: documentIds,
        subject,
        module_count: moduleCount,
        audience,
      }),
    onSuccess: (course) => {
      toast.success("Generation queued", {
        description: "The worker builds the course; the row updates when it finishes.",
      });
      setOpen(false);
      setDocumentIds([]);
      setSubject("");
      void queryClient.invalidateQueries({ queryKey: ["courses"] });
      void course;
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not start generation"),
  });

  return (
    <>
      <PageHeader
        title="Courses"
        description="Generated courses land in review. Nothing is published without a human approving it."
        actions={<Button onClick={() => setOpen(true)}>Generate course</Button>}
      />

      {courses.error ? (
        <ErrorState error={courses.error} retry={() => courses.refetch()} />
      ) : courses.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !courses.data?.items.length ? (
        <EmptyState
          title="No courses yet"
          description="Generate one from indexed documents, or create an empty course and write it by hand."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              Generate course
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Course</Th>
                <Th>Status</Th>
                <Th>Model</Th>
                <Th>Prompt v.</Th>
                <Th>Published</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {courses.data.items.map((course) => (
                <tr key={course.id} className="hover:bg-muted/50">
                  <Td>
                    <Link
                      href={`/dashboard/courses/${course.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {course.title}
                    </Link>
                    {course.description && (
                      <p className="max-w-md truncate text-2xs text-muted-foreground">
                        {course.description}
                      </p>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={course.status} />
                  </Td>
                  <Td className="text-muted-foreground">{course.model_used ?? "—"}</Td>
                  <Td className="tabular text-muted-foreground">{course.prompt_version ?? "—"}</Td>
                  <Td className="text-muted-foreground">{dateTime(course.published_at)}</Td>
                  <Td className="text-muted-foreground">{dateTime(course.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Generate a course">
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
            <Label htmlFor="kb">Knowledge base</Label>
            <Select
              id="kb"
              className="w-full"
              value={knowledgeBaseId}
              onChange={(event) => {
                setKnowledgeBaseId(event.target.value);
                setDocumentIds([]);
              }}
              required
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
            <Label>Source documents (indexed only)</Label>
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {!knowledgeBaseId && (
                <p className="text-2xs text-muted-foreground">Select a knowledge base first.</p>
              )}
              {knowledgeBaseId && !documents.data?.items.length && (
                <p className="text-2xs text-muted-foreground">
                  No indexed documents in this knowledge base yet.
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
            <Label htmlFor="subject">Subject</Label>
            <Input
              id="subject"
              required
              minLength={3}
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="Remote work policy"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="modules">Modules</Label>
              <Input
                id="modules"
                type="number"
                min={1}
                max={10}
                value={moduleCount}
                onChange={(event) => setModuleCount(Number(event.target.value))}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="audience">Audience</Label>
              <Input
                id="audience"
                value={audience}
                onChange={(event) => setAudience(event.target.value)}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={generate.isPending}>
              Generate
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
