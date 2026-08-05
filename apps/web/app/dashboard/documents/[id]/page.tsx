"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Progress,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api, subscribe } from "@/lib/api";
import { bytes, dateTime, duration, number } from "@/lib/format";
import type { DocumentDetail, Page as PageType } from "@/lib/types";

interface ChunkRow {
  id: string;
  position: number;
  content: string;
  token_count: number;
  page_number: number | null;
  section: string | null;
}

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [live, setLive] = React.useState<{ stage?: string; progress?: number } | null>(null);

  const document = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<DocumentDetail>(`/api/v1/documents/${id}`),
  });

  const chunks = useQuery({
    queryKey: ["document", id, "chunks"],
    queryFn: () => api.get<PageType<ChunkRow>>(`/api/v1/documents/${id}/chunks`, { limit: 20 }),
    enabled: document.data?.status === "indexed",
  });

  const inFlight =
    document.data?.status === "pending" || document.data?.status === "processing";

  // Live pipeline progress over SSE. The query is still the source of truth; the stream
  // just avoids polling and gives per-stage transitions as they happen.
  React.useEffect(() => {
    if (!inFlight) return;
    const stop = subscribe(`/api/v1/documents/${id}/stream`, (event, data) => {
      const payload = data as { stage?: string; progress?: number };
      if (event === "progress") setLive(payload);
      if (event === "done") {
        setLive(null);
        void queryClient.invalidateQueries({ queryKey: ["document", id] });
      }
    });
    return stop;
  }, [id, inFlight, queryClient]);

  if (document.error) return <ErrorState error={document.error} retry={() => document.refetch()} />;
  if (document.isLoading || !document.data) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  const doc = document.data;
  const progress = live?.progress ?? doc.progress;

  return (
    <>
      <PageHeader
        title={doc.filename}
        description={`${doc.content_type} · ${bytes(doc.size_bytes)} · uploaded ${dateTime(doc.created_at)}`}
        actions={
          <Link href="/dashboard/documents" className="text-xs text-primary hover:underline">
            ← All documents
          </Link>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Ingestion pipeline</CardTitle>
            <div className="flex items-center gap-2">
              {inFlight && <Badge tone="warning">live</Badge>}
              <StatusBadge status={doc.status} />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <Progress value={progress} label="Ingestion progress" />
            {live?.stage && (
              <p className="text-2xs text-muted-foreground">Current stage: {live.stage}</p>
            )}
            {doc.error && (
              <div className="rounded-md border border-danger/30 bg-danger/5 p-3">
                <p className="text-xs font-medium text-danger">Pipeline failed</p>
                <p className="mt-1 text-2xs text-muted-foreground">{doc.error}</p>
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-2"
                  onClick={async () => {
                    await api.post(`/api/v1/documents/${id}/reindex`);
                    void queryClient.invalidateQueries({ queryKey: ["document", id] });
                  }}
                >
                  Retry ingestion
                </Button>
              </div>
            )}
            <Table>
              <thead>
                <tr>
                  <Th>Stage</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Attempt</Th>
                  <Th className="text-right">Duration</Th>
                  <Th>Details</Th>
                </tr>
              </thead>
              <tbody>
                {doc.runs.map((run) => (
                  <tr key={run.id}>
                    <Td className="font-medium">{run.stage}</Td>
                    <Td>
                      <StatusBadge status={run.status} />
                    </Td>
                    <Td className="text-right tabular">{run.attempt}</Td>
                    <Td className="text-right tabular text-muted-foreground">
                      {duration(run.duration_ms)}
                    </Td>
                    <Td className="max-w-xs truncate text-2xs text-muted-foreground">
                      {run.error ?? JSON.stringify(run.details)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Extracted metadata</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row label="Chunks" value={number(doc.chunk_count)} />
            <Row label="Words" value={number(doc.word_count)} />
            <Row label="Pages" value={number(doc.page_count)} />
            <Row label="Language" value={doc.detected_language ?? "—"} />
            <Row label="Indexed" value={dateTime(doc.indexed_at)} />
          </CardContent>
        </Card>
      </div>

      {doc.status === "indexed" && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Stored chunks (first 20)</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {chunks.isLoading ? (
              <div className="space-y-2 p-4">
                <Skeleton />
                <Skeleton />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th className="w-12 text-right">#</Th>
                    <Th className="text-right">Tokens</Th>
                    <Th className="text-right">Page</Th>
                    <Th>Section</Th>
                    <Th>Content</Th>
                  </tr>
                </thead>
                <tbody>
                  {chunks.data?.items.map((chunk) => (
                    <tr key={chunk.id}>
                      <Td className="text-right tabular text-muted-foreground">{chunk.position}</Td>
                      <Td className="text-right tabular">{chunk.token_count}</Td>
                      <Td className="text-right tabular text-muted-foreground">
                        {chunk.page_number ?? "—"}
                      </Td>
                      <Td className="text-muted-foreground">{chunk.section ?? "—"}</Td>
                      <Td className="max-w-xl text-2xs text-muted-foreground">
                        {chunk.content.slice(0, 220)}
                        {chunk.content.length > 220 && "…"}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="tabular text-sm font-medium">{value}</span>
    </div>
  );
}
