"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PageHeader } from "@/components/shell";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Metric,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { compactNumber, currency, dateTime, duration, number, percent } from "@/lib/format";
import type { Execution, Overview, Timeseries, WorkflowStats } from "@/lib/types";

export default function DashboardPage() {
  const overview = useQuery({
    queryKey: ["analytics", "overview"],
    queryFn: () => api.get<Overview>("/api/v1/analytics/overview"),
  });
  const series = useQuery({
    queryKey: ["analytics", "timeseries", "ai_requests"],
    queryFn: () =>
      api.get<Timeseries>("/api/v1/analytics/timeseries", { metric: "ai_requests", days: 30 }),
  });
  const executions = useQuery({
    queryKey: ["executions", "recent"],
    queryFn: () => api.get<{ items: Execution[] }>("/api/v1/executions", { limit: 8 }),
    refetchInterval: 15_000,
  });
  const workflows = useQuery({
    queryKey: ["analytics", "workflows"],
    queryFn: () => api.get<WorkflowStats[]>("/api/v1/analytics/workflows"),
  });

  const data = overview.data;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Live figures for this organization. Empty values mean no data yet, not a broken widget."
      />

      {overview.error ? (
        <ErrorState error={overview.error} retry={() => overview.refetch()} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Learners"
            value={number(data?.total_learners)}
            hint={`${number(data?.active_learners)} active in 14 days`}
            loading={overview.isLoading}
          />
          <Metric
            label="Completion rate"
            value={percent(data?.course_completion_rate)}
            hint="Enrolments finished"
            loading={overview.isLoading}
          />
          <Metric
            label="Avg assessment score"
            value={data?.average_assessment_score != null ? `${data.average_assessment_score}%` : "—"}
            hint="Across graded submissions"
            loading={overview.isLoading}
          />
          <Metric
            label="Open tickets"
            value={number(data?.open_support_tickets)}
            hint="Not resolved or closed"
            loading={overview.isLoading}
          />
          <Metric
            label="Documents indexed"
            value={number(data?.documents_indexed)}
            hint="Searchable in the knowledge base"
            loading={overview.isLoading}
          />
          <Metric
            label="AI requests (30d)"
            value={compactNumber(data?.ai_requests)}
            hint={`${compactNumber(data?.tokens_consumed)} tokens`}
            loading={overview.isLoading}
          />
          <Metric
            label="Estimated model cost"
            value={currency(data?.estimated_cost_usd)}
            hint="Last 30 days"
            loading={overview.isLoading}
          />
          <Metric
            label="Workflow success"
            value={percent(data?.workflow_success_rate)}
            hint={`${percent(data?.workflow_failure_rate)} failed · avg ${duration(
              data?.average_workflow_duration_ms,
            )}`}
            loading={overview.isLoading}
          />
        </div>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>AI requests, last 30 days</CardTitle>
          </CardHeader>
          <CardContent>
            {series.isLoading ? (
              <Skeleton className="h-56" />
            ) : !series.data?.points.length ? (
              <EmptyState
                title="No AI activity yet"
                description="Ask the assistant a question or generate a course to start recording usage."
              />
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={series.data.points} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                    <defs>
                      <linearGradient id="requests" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10 }}
                      stroke="hsl(var(--muted-foreground))"
                      tickFormatter={(value: string) => value.slice(5)}
                    />
                    <YAxis tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" allowDecimals={false} />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: 6,
                        fontSize: 12,
                      }}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      name="requests"
                      stroke="hsl(var(--primary))"
                      fill="url(#requests)"
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latency</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <LatencyRow label="LLM response" value={data?.llm_response_latency_ms} />
            <LatencyRow label="RAG answer" value={data?.rag_retrieval_latency_ms} />
            <LatencyRow label="Workflow run" value={data?.average_workflow_duration_ms} />
            <p className="pt-2 text-2xs text-muted-foreground">
              Averages over the last 30 days of recorded calls.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent workflow runs</CardTitle>
            <Link href="/dashboard/executions" className="text-xs text-primary hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {executions.isLoading ? (
              <div className="space-y-2 p-4">
                <Skeleton />
                <Skeleton />
                <Skeleton />
              </div>
            ) : !executions.data?.items.length ? (
              <div className="p-4">
                <EmptyState
                  title="No runs yet"
                  description="Add a learner or trigger a workflow to see executions here."
                />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Workflow</Th>
                    <Th>Status</Th>
                    <Th>Duration</Th>
                    <Th>Started</Th>
                  </tr>
                </thead>
                <tbody>
                  {executions.data.items.map((execution) => (
                    <tr key={execution.id} className="hover:bg-muted/50">
                      <Td>
                        <Link
                          href={`/dashboard/executions/${execution.id}`}
                          className="text-primary hover:underline"
                        >
                          {execution.workflow_name ?? execution.workflow_id.slice(0, 8)}
                        </Link>
                      </Td>
                      <Td>
                        <StatusBadge status={execution.status} />
                      </Td>
                      <Td className="text-muted-foreground">{duration(execution.duration_ms)}</Td>
                      <Td className="text-muted-foreground">{dateTime(execution.created_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Workflow reliability</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {workflows.isLoading ? (
              <div className="space-y-2 p-4">
                <Skeleton />
                <Skeleton />
              </div>
            ) : !workflows.data?.length ? (
              <div className="p-4">
                <EmptyState
                  title="No workflows configured"
                  description="Create one from a template on the Workflows page."
                />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Workflow</Th>
                    <Th className="text-right">Runs</Th>
                    <Th className="text-right">Success</Th>
                    <Th className="text-right">Avg</Th>
                  </tr>
                </thead>
                <tbody>
                  {workflows.data.map((row) => (
                    <tr key={row.workflow_id} className="hover:bg-muted/50">
                      <Td>{row.name}</Td>
                      <Td className="text-right tabular">{row.total}</Td>
                      <Td className="text-right tabular">
                        {row.total ? percent(row.success_rate, 0) : "—"}
                      </Td>
                      <Td className="text-right tabular text-muted-foreground">
                        {duration(row.avg_duration_ms)}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function LatencyRow({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular font-medium">{duration(value)}</span>
    </div>
  );
}
