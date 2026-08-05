"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Progress,
  Select,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { bytes, dateTime, number } from "@/lib/format";
import type { DocumentDetail, DocumentSummary, KnowledgeBase, Page } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.txt,.md,.csv";

function DocumentsView() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const fileInput = React.useRef<HTMLInputElement>(null);
  const [knowledgeBaseId, setKnowledgeBaseId] = React.useState(
    params.get("knowledge_base_id") ?? "",
  );

  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
  });

  React.useEffect(() => {
    if (!knowledgeBaseId && bases.data?.items.length) {
      setKnowledgeBaseId(bases.data.items[0].id);
    }
  }, [bases.data, knowledgeBaseId]);

  const documents = useQuery({
    queryKey: ["documents", knowledgeBaseId],
    queryFn: () =>
      api.get<Page<DocumentSummary>>("/api/v1/documents", {
        limit: 50,
        knowledge_base_id: knowledgeBaseId || undefined,
      }),
    // Ingestion runs on the worker; poll while anything is mid-flight.
    refetchInterval: (query) =>
      query.state.data?.items.some((d) => d.status === "pending" || d.status === "processing")
        ? 2500
        : false,
  });

  const upload = useMutation({
    mutationFn: (file: File) =>
      api.upload<DocumentDetail>("/api/v1/documents", file, {
        knowledge_base_id: knowledgeBaseId,
      }),
    onSuccess: (document) => {
      toast.success(`${document.filename} queued for ingestion`);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (error) =>
      toast.error(error instanceof ApiError ? error.message : "Upload failed", {
        description:
          error instanceof ApiError && error.code === "conflict"
            ? "That exact file is already in this knowledge base."
            : undefined,
      }),
  });

  const reindex = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/documents/${id}/reindex`),
    onSuccess: () => {
      toast.success("Re-ingestion queued");
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Retry failed"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/documents/${id}`),
    onSuccess: () => {
      toast.success("Document and its vectors deleted");
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });

  const noBases = !bases.isLoading && !bases.data?.items.length;

  return (
    <>
      <PageHeader
        title="Documents"
        description="PDF, DOCX, TXT, Markdown and CSV. Each upload runs the ten-stage ingestion pipeline."
        actions={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Knowledge base"
              value={knowledgeBaseId}
              onChange={(e) => setKnowledgeBaseId(e.target.value)}
              disabled={noBases}
            >
              {bases.data?.items.map((kb) => (
                <option key={kb.id} value={kb.id}>
                  {kb.name}
                </option>
              ))}
            </Select>
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload.mutate(file);
                event.target.value = "";
              }}
            />
            <Button
              onClick={() => fileInput.current?.click()}
              loading={upload.isPending}
              disabled={!knowledgeBaseId}
            >
              Upload document
            </Button>
          </div>
        }
      />

      {noBases ? (
        <EmptyState
          title="Create a knowledge base first"
          description="Documents are always uploaded into a knowledge base."
          action={
            <Link
              href="/dashboard/knowledge-bases"
              className="mt-2 text-xs text-primary hover:underline"
            >
              Go to knowledge bases
            </Link>
          }
        />
      ) : documents.error ? (
        <ErrorState error={documents.error} retry={() => documents.refetch()} />
      ) : documents.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !documents.data?.items.length ? (
        <EmptyState
          title="No documents yet"
          description="Upload a policy, handbook or training deck to make it searchable."
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>File</Th>
                <Th>Status</Th>
                <Th className="w-48">Pipeline</Th>
                <Th className="text-right">Words</Th>
                <Th className="text-right">Size</Th>
                <Th>Uploaded</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {documents.data.items.map((document) => (
                <tr key={document.id} className="hover:bg-muted/50">
                  <Td>
                    <Link
                      href={`/dashboard/documents/${document.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {document.filename}
                    </Link>
                    {document.error && (
                      <p className="text-2xs text-danger" title={document.error}>
                        {document.error.slice(0, 90)}
                      </p>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={document.status} />
                  </Td>
                  <Td>
                    <Progress value={document.progress} label={`${document.filename} progress`} />
                    <p className="text-2xs text-muted-foreground">{document.stage}</p>
                  </Td>
                  <Td className="text-right tabular">{number(document.word_count)}</Td>
                  <Td className="text-right tabular text-muted-foreground">
                    {bytes(document.size_bytes)}
                  </Td>
                  <Td className="text-muted-foreground">{dateTime(document.created_at)}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={reindex.isPending && reindex.variables === document.id}
                      onClick={() => reindex.mutate(document.id)}
                    >
                      Re-index
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        if (confirm(`Delete ${document.filename} and its vectors?`)) {
                          remove.mutate(document.id);
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
    </>
  );
}

// useSearchParams opts the tree into client-side rendering, so Next requires a Suspense
// boundary above it for the prerender pass.
export default function DocumentsPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-40" />}>
      <DocumentsView />
    </React.Suspense>
  );
}
