"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Skeleton,
} from "@/components/ui";
import { api } from "@/lib/api";
import { number } from "@/lib/format";
import type { Integrations } from "@/lib/types";

export default function IntegrationsPage() {
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api.get<Integrations>("/api/v1/integrations"),
    refetchInterval: 30_000,
  });

  if (integrations.error) {
    return <ErrorState error={integrations.error} retry={() => integrations.refetch()} />;
  }

  const data = integrations.data;

  return (
    <>
      <PageHeader
        title="Integrations"
        description="Live status of the services this platform depends on. Values come from the running configuration, not a static list."
      />

      {integrations.isLoading || !data ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((index) => (
            <Card key={index}>
              <CardContent className="space-y-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-3 w-40" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Service
            title="LLM provider"
            healthy={data.llm.configured}
            rows={[
              ["Provider", data.llm.provider],
              ["Model", data.llm.model],
              ["Fallback", data.llm.fallback ?? "none"],
            ]}
            note={
              data.llm.provider === "mock"
                ? "Running the deterministic offline provider — no API key needed, no calls leave the machine."
                : undefined
            }
          />
          <Service
            title="Embeddings"
            healthy
            rows={[
              ["Provider", data.embeddings.provider],
              ["Model", data.embeddings.model],
            ]}
          />
          <Service
            title="Vector store"
            healthy={data.vector_store.healthy}
            rows={[
              ["Engine", data.vector_store.engine],
              ["Vectors in this org", number(data.vector_store.vectors)],
            ]}
            note="Isolation is per-organization collection, not a metadata filter."
          />
          <Service title="Redis" healthy={data.redis.healthy} rows={[["Queue and pub/sub", "ARQ"]]} />
          <Service
            title="n8n"
            healthy={data.n8n.healthy}
            rows={[["Base URL", data.n8n.base_url]]}
            note={
              data.n8n.healthy
                ? "Workflows with a webhook path are handed to n8n."
                : "Not reachable — workflows fall back to the built-in runner."
            }
          />
          <Service
            title="Email"
            healthy
            rows={[["Adapter", data.email.adapter]]}
            note={
              data.email.adapter === "mock"
                ? "Messages are logged and retained in memory; nothing is actually sent."
                : undefined
            }
          />
        </div>
      )}
    </>
  );
}

function Service({
  title,
  healthy,
  rows,
  note,
}: {
  title: string;
  healthy: boolean;
  rows: Array<[string, string]>;
  note?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>{title}</CardTitle>
        <Badge tone={healthy ? "success" : "danger"}>{healthy ? "healthy" : "unavailable"}</Badge>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3 text-xs">
            <span className="text-muted-foreground">{label}</span>
            <span className="truncate font-medium">{value}</span>
          </div>
        ))}
        {note && <p className="pt-1 text-2xs text-muted-foreground">{note}</p>}
      </CardContent>
    </Card>
  );
}
