"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Select,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { currency, dateTime, duration, number } from "@/lib/format";
import type { Execution } from "@/lib/types";

const STATUSES = ["", "queued", "running", "succeeded", "failed", "cancelled"] as const;

export default function ExecutionsPage() {
  const [status, setStatus] = React.useState<string>("");

  const query = useQuery({
    queryKey: ["executions", status],
    queryFn: () =>
      api.get<{ items: Execution[]; total: number }>("/api/v1/executions", {
        limit: 50,
        status: status || undefined,
      }),
    // Anything in flight changes on the worker, not in this tab.
    refetchInterval: (q) =>
      q.state.data?.items.some((e) => e.status === "running" || e.status === "queued")
        ? 4000
        : false,
  });

  return (
    <>
      <PageHeader
        title="Workflow executions"
        description="Every run, whether it executed in n8n or in the built-in runner."
        actions={
          <Select
            aria-label="Status filter"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {value === "" ? "All statuses" : value}
              </option>
            ))}
          </Select>
        }
      />

      {query.error ? (
        <ErrorState error={query.error} retry={() => query.refetch()} />
      ) : query.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !query.data?.items.length ? (
        <EmptyState
          title="No executions"
          description="Trigger a workflow, or add a learner to fire the onboarding workflow."
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Workflow</Th>
                <Th>Status</Th>
                <Th>Trigger</Th>
                <Th className="text-right">Duration</Th>
                <Th className="text-right">Tokens</Th>
                <Th className="text-right">Cost</Th>
                <Th className="text-right">Retries</Th>
                <Th>n8n id</Th>
                <Th>Started</Th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((execution) => (
                <tr key={execution.id} className="hover:bg-muted/50">
                  <Td>
                    <Link
                      href={`/dashboard/executions/${execution.id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {execution.workflow_name ?? execution.workflow_id.slice(0, 8)}
                    </Link>
                    {execution.error && (
                      <p className="max-w-sm truncate text-2xs text-danger">{execution.error}</p>
                    )}
                  </Td>
                  <Td>
                    <StatusBadge status={execution.status} />
                  </Td>
                  <Td className="text-muted-foreground">{execution.trigger_type}</Td>
                  <Td className="text-right tabular text-muted-foreground">
                    {duration(execution.duration_ms)}
                  </Td>
                  <Td className="text-right tabular">
                    {number(execution.input_tokens + execution.output_tokens)}
                  </Td>
                  <Td className="text-right tabular">{currency(execution.estimated_cost_usd, 6)}</Td>
                  <Td className="text-right tabular">{execution.retry_count}</Td>
                  <Td className="text-2xs text-muted-foreground">
                    {execution.n8n_execution_id ?? "—"}
                  </Td>
                  <Td className="text-muted-foreground">{dateTime(execution.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
