"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api, subscribe } from "@/lib/api";
import { currency, dateTime, duration, number } from "@/lib/format";
import type { ExecutionDetail } from "@/lib/types";

const LIVE_STATUSES = new Set(["queued", "running", "waiting_approval"]);

export default function ExecutionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [liveEvents, setLiveEvents] = React.useState<string[]>([]);

  const execution = useQuery({
    queryKey: ["execution", id],
    queryFn: () => api.get<ExecutionDetail>(`/api/v1/executions/${id}`),
  });

  const isLive = execution.data ? LIVE_STATUSES.has(execution.data.status) : false;

  React.useEffect(() => {
    if (!isLive) return;
    const stop = subscribe(`/api/v1/executions/${id}/stream`, (event, data) => {
      if (event === "ping") return;
      setLiveEvents((previous) => [
        `${new Date().toLocaleTimeString()}  ${event}  ${JSON.stringify(data)}`,
        ...previous.slice(0, 40),
      ]);
      void queryClient.invalidateQueries({ queryKey: ["execution", id] });
    });
    return stop;
  }, [id, isLive, queryClient]);

  const retry = useMutation({
    mutationFn: () => api.post(`/api/v1/executions/${id}/retry`),
    onSuccess: () => {
      toast.success("Retry queued");
      void queryClient.invalidateQueries({ queryKey: ["execution", id] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Retry failed"),
  });

  if (execution.error) return <ErrorState error={execution.error} retry={() => execution.refetch()} />;
  if (execution.isLoading || !execution.data) {
    return (
      <Card>
        <CardContent className="space-y-3">
          <Skeleton className="h-6 w-64" />
          <Skeleton className="h-40" />
        </CardContent>
      </Card>
    );
  }

  const run = execution.data;
  const canRetry = run.status === "failed" || run.status === "cancelled";

  return (
    <>
      <PageHeader
        title={run.workflow_name ?? "Workflow run"}
        description={`Execution ${run.id}`}
        actions={
          <div className="flex items-center gap-2">
            <Link href="/dashboard/executions" className="text-xs text-primary hover:underline">
              ← All executions
            </Link>
            {isLive && <Badge tone="warning">live</Badge>}
            <StatusBadge status={run.status} />
            {canRetry && (
              <Button loading={retry.isPending} onClick={() => retry.mutate()}>
                Retry run
              </Button>
            )}
          </div>
        }
      />

      {run.error && (
        <div className="mb-4 rounded-md border border-danger/30 bg-danger/5 p-3">
          <p className="text-xs font-medium text-danger">Execution failed</p>
          <p className="mt-1 whitespace-pre-wrap text-2xs text-muted-foreground">{run.error}</p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Run</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <Row label="Trigger" value={run.trigger_type} />
            <Row label="Triggered by" value={run.triggered_by ?? "—"} />
            <Row label="Started" value={dateTime(run.started_at)} />
            <Row label="Finished" value={dateTime(run.finished_at)} />
            <Row label="Duration" value={duration(run.duration_ms)} />
            <Row label="Retries" value={run.retry_count} />
            <Row label="n8n execution" value={run.n8n_execution_id ?? "—"} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI cost</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <Row label="Input tokens" value={number(run.input_tokens)} />
            <Row label="Output tokens" value={number(run.output_tokens)} />
            <Row label="Estimated cost" value={currency(run.estimated_cost_usd, 6)} />
            <p className="pt-2 text-2xs text-muted-foreground">
              Attributed by foreign key from every model call made during this run.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Related</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <Row
              label="Learner"
              value={
                run.learner_id ? (
                  <Link
                    href={`/dashboard/learners?id=${run.learner_id}`}
                    className="text-primary hover:underline"
                  >
                    {run.learner_id.slice(0, 8)}
                  </Link>
                ) : (
                  "—"
                )
              }
            />
            <Row
              label="Course"
              value={
                run.course_id ? (
                  <Link
                    href={`/dashboard/courses/${run.course_id}`}
                    className="text-primary hover:underline"
                  >
                    {run.course_id.slice(0, 8)}
                  </Link>
                ) : (
                  "—"
                )
              }
            />
            <Row label="Workflow key" value={run.workflow_key ?? "—"} />
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Steps</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {run.steps.length === 0 ? (
            <p className="p-4 text-xs text-muted-foreground">No steps recorded yet.</p>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th className="w-10 text-right">#</Th>
                  <Th>Step</Th>
                  <Th>Kind</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Attempt</Th>
                  <Th className="text-right">Duration</Th>
                  <Th>Output / error</Th>
                </tr>
              </thead>
              <tbody>
                {run.steps.map((step) => (
                  <tr key={step.id}>
                    <Td className="text-right tabular text-muted-foreground">{step.position}</Td>
                    <Td className="font-medium">{step.name}</Td>
                    <Td className="text-muted-foreground">{step.kind}</Td>
                    <Td>
                      <StatusBadge status={step.status} />
                    </Td>
                    <Td className="text-right tabular">{step.attempt}</Td>
                    <Td className="text-right tabular text-muted-foreground">
                      {duration(step.duration_ms)}
                    </Td>
                    <Td className="max-w-md truncate text-2xs text-muted-foreground">
                      {step.error ?? JSON.stringify(step.output)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Payload title="Input payload" value={run.input_payload} />
        <Payload title="Output payload" value={run.output_payload} />
      </div>

      {isLive && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Live event stream</CardTitle>
          </CardHeader>
          <CardContent>
            {liveEvents.length === 0 ? (
              <p className="text-2xs text-muted-foreground">Waiting for the next event…</p>
            ) : (
              <pre className="max-h-56 overflow-auto rounded bg-muted p-3 text-2xs leading-relaxed">
                {liveEvents.join("\n")}
              </pre>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular truncate font-medium">{value}</span>
    </div>
  );
}

function Payload({ title, value }: { title: string; value: Record<string, unknown> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="max-h-64 overflow-auto rounded bg-muted p-3 text-2xs leading-relaxed">
          {JSON.stringify(value, null, 2)}
        </pre>
      </CardContent>
    </Card>
  );
}
