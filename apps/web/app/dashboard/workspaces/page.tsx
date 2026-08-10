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
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Page, Workspace } from "@/lib/types";

const SLUG = /^[a-z0-9-]+$/;

export default function WorkspacesPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState({ name: "", slug: "", description: "" });

  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Page<Workspace>>("/api/v1/workspaces", { limit: 100 }),
  });

  const create = useMutation({
    mutationFn: () => api.post<Workspace>("/api/v1/workspaces", form),
    onSuccess: (workspace) => {
      toast.success(`Workspace "${workspace.name}" created`);
      setOpen(false);
      setForm({ name: "", slug: "", description: "" });
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Create failed"),
  });

  const slugValid = SLUG.test(form.slug);

  return (
    <>
      <PageHeader
        title="Workspaces"
        description="Sub-divisions inside this organization. Knowledge bases, courses and learners can each belong to one, and the dashboard can be filtered by it."
        actions={<Button onClick={() => setOpen(true)}>New workspace</Button>}
      />

      {workspaces.error ? (
        <ErrorState error={workspaces.error} retry={() => workspaces.refetch()} />
      ) : workspaces.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !workspaces.data?.items.length ? (
        <EmptyState
          title="No workspaces yet"
          description="Create one to group learners and content by team, region or programme."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              New workspace
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Slug</Th>
                <Th>Description</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {workspaces.data.items.map((workspace) => (
                <tr key={workspace.id} className="hover:bg-muted/50">
                  <Td className="font-medium">{workspace.name}</Td>
                  <Td>
                    <code className="rounded bg-muted px-1 py-0.5 text-2xs">{workspace.slug}</code>
                  </Td>
                  <Td className="text-muted-foreground">{workspace.description ?? "—"}</Td>
                  <Td className="text-muted-foreground">{dateTime(workspace.created_at)}</Td>
                  <Td className="text-right">
                    <Link
                      href="/dashboard"
                      className="text-xs text-primary hover:underline"
                      title="Filter the dashboard by this workspace"
                    >
                      View metrics
                    </Link>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New workspace">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="ws-name">Name</Label>
            <Input
              id="ws-name"
              required
              minLength={2}
              value={form.name}
              onChange={(event) => {
                const name = event.target.value;
                setForm((previous) => ({
                  ...previous,
                  name,
                  // Keep the slug in step until the user edits it themselves.
                  slug: SLUG.test(previous.slug) && previous.slug !== slugify(previous.name)
                    ? previous.slug
                    : slugify(name),
                }));
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ws-slug">Slug</Label>
            <Input
              id="ws-slug"
              required
              value={form.slug}
              onChange={(event) => setForm({ ...form, slug: event.target.value })}
            />
            {form.slug && !slugValid && (
              <p role="alert" className="text-2xs text-danger">
                Lowercase letters, digits and hyphens only.
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="ws-description">Description</Label>
            <Input
              id="ws-description"
              value={form.description}
              onChange={(event) => setForm({ ...form, description: event.target.value })}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending} disabled={!slugValid}>
              Create
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}
