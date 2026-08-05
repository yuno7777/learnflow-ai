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
import { dateTime, number } from "@/lib/format";
import type { KnowledgeBase, Page } from "@/lib/types";

export default function KnowledgeBasesPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [form, setForm] = React.useState({
    name: "",
    description: "",
    chunk_size_tokens: 700,
    chunk_overlap_tokens: 100,
  });

  const query = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
  });

  const create = useMutation({
    mutationFn: () => api.post<KnowledgeBase>("/api/v1/knowledge-bases", form),
    onSuccess: (kb) => {
      toast.success(`Knowledge base "${kb.name}" created`);
      setOpen(false);
      setForm({ name: "", description: "", chunk_size_tokens: 700, chunk_overlap_tokens: 100 });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Could not create knowledge base"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/knowledge-bases/${id}`),
    onSuccess: () => {
      toast.success("Knowledge base deleted with all of its vectors");
      void queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Delete failed"),
  });

  return (
    <>
      <PageHeader
        title="Knowledge bases"
        description="Each knowledge base owns its chunking settings and its slice of the vector store."
        actions={<Button onClick={() => setOpen(true)}>New knowledge base</Button>}
      />

      {query.error ? (
        <ErrorState error={query.error} retry={() => query.refetch()} />
      ) : query.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !query.data?.items.length ? (
        <EmptyState
          title="No knowledge bases yet"
          description="Create one, then upload training material to make it searchable."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              New knowledge base
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Embedding model</Th>
                <Th className="text-right">Documents</Th>
                <Th className="text-right">Chunks</Th>
                <Th className="text-right">Chunk size</Th>
                <Th>Created</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((kb) => (
                <tr key={kb.id} className="hover:bg-muted/50">
                  <Td>
                    <Link
                      href={`/dashboard/documents?knowledge_base_id=${kb.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {kb.name}
                    </Link>
                    {kb.description && (
                      <p className="text-2xs text-muted-foreground">{kb.description}</p>
                    )}
                  </Td>
                  <Td className="text-muted-foreground">{kb.embedding_model}</Td>
                  <Td className="text-right tabular">{number(kb.document_count)}</Td>
                  <Td className="text-right tabular">{number(kb.chunk_count)}</Td>
                  <Td className="text-right tabular text-muted-foreground">
                    {kb.chunk_size_tokens}/{kb.chunk_overlap_tokens}
                  </Td>
                  <Td className="text-muted-foreground">{dateTime(kb.created_at)}</Td>
                  <Td className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={remove.isPending && remove.variables === kb.id}
                      onClick={() => {
                        if (confirm(`Delete "${kb.name}" and every document and vector in it?`)) {
                          remove.mutate(kb.id);
                        }
                      }}
                    >
                      Delete
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New knowledge base">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="kb-name">Name</Label>
            <Input
              id="kb-name"
              required
              minLength={2}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="kb-description">Description</Label>
            <Input
              id="kb-description"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="kb-chunk">Chunk size (tokens)</Label>
              <Input
                id="kb-chunk"
                type="number"
                min={100}
                max={2000}
                value={form.chunk_size_tokens}
                onChange={(e) => setForm({ ...form, chunk_size_tokens: Number(e.target.value) })}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="kb-overlap">Overlap (tokens)</Label>
              <Input
                id="kb-overlap"
                type="number"
                min={0}
                max={500}
                value={form.chunk_overlap_tokens}
                onChange={(e) => setForm({ ...form, chunk_overlap_tokens: Number(e.target.value) })}
              />
            </div>
          </div>
          <p className="text-2xs text-muted-foreground">
            Larger chunks preserve context; smaller chunks retrieve more precisely. Overlap keeps
            facts that straddle a boundary findable from both sides.
          </p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
