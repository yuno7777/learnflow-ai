"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import {
  Bar,
  BarChart,
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
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { duration, number, percent, relativeTime } from "@/lib/format";
import type { InactiveLearnersResponse, Overview, Timeseries, WorkflowStats } from "@/lib/types";

type MetricKey = "ai_requests" | "cost" | "tokens" | "executions" | "submissions";

export default function AnalyticsPage() {
  const [metric, setMetric] = React.useState<MetricKey>("executions");
  const [days, setDays] = React.useState(30);
  const [threshold, setThreshold] = React.useState(14);

  const overview = useQuery({
    queryKey: ["analytics", "overview"],
    queryFn: () => api.get<Overview>("/api/v1/analytics/overview"),
  });
  const series = useQuery({
    queryKey: ["analytics", "timeseries", metric, days],
    queryFn: () => api.get<Timeseries>("/api/v1/analytics/timeseries", { metric, days }),
  });
  const workflows = useQuery({
    queryKey: ["analytics", "workflows"],
    queryFn: () => api.get<WorkflowStats[]>("/api/v1/analytics/workflows"),
  });
  const evaluations = useQuery({
    queryKey: ["analytics", "evaluations"],
    queryFn: () =>
      api.get<{ total: number; needs_review: number; average_confidence: number | null }>(
        "/api/v1/analytics/evaluations",
      ),
  });
  const inactive = useQuery({
    queryKey: ["analytics", "inactive", threshold],
    queryFn: () =>
      api.get<InactiveLearnersResponse>("/api/v1/analytics/inactive-learners", {
        threshold_days: threshold,
      }),
  });

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Learning, automation and evaluation health for this organization."
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Active learners"
          value={number(overview.data?.active_learners)}
          hint={`of ${number(overview.data?.total_learners)} total`}
          loading={overview.isLoading}
        />
        <Metric
          label="Completion rate"
          value={percent(overview.data?.course_completion_rate)}
          loading={overview.isLoading}
        />
        <Metric
          label="Evaluations awaiting review"
          value={number(evaluations.data?.needs_review)}
          hint={`of ${number(evaluations.data?.total)} graded`}
          loading={evaluations.isLoading}
        />
        <Metric
          label="Avg evaluation confidence"
          value={evaluations.data?.average_confidence?.toFixed(2) ?? "—"}
          loading={evaluations.isLoading}
        />
      </div>

      <Card className="mt-4">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Trend</CardTitle>
          <div className="flex gap-2">
            <Select
              aria-label="Metric"
              value={metric}
              onChange={(event) => setMetric(event.target.value as MetricKey)}
            >
              <option value="executions">Workflow executions</option>
              <option value="ai_requests">AI requests</option>
              <option value="tokens">Tokens</option>
              <option value="cost">Cost</option>
              <option value="submissions">Submissions</option>
            </Select>
            <Select
              aria-label="Range"
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {series.error ? (
            <ErrorState error={series.error} retry={() => series.refetch()} />
          ) : series.isLoading ? (
            <Skeleton className="h-56" />
          ) : !series.data?.points.length ? (
            <EmptyState title="No data in this range" />
          ) : (
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={series.data.points} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                  <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10 }}
                    stroke="hsl(var(--muted-foreground))"
                    tickFormatter={(value: string) => value.slice(5)}
                  />
                  <YAxis tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
                  <Tooltip
                    contentStyle={{
                      background: "hsl(var(--card))",
                      border: "1px solid hsl(var(--border))",
                      borderRadius: 6,
                      fontSize: 12,
                    }}
                  />
                  <Bar dataKey="value" name={metric} fill="hsl(var(--primary))" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
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
                <EmptyState title="No workflows configured" />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Workflow</Th>
                    <Th className="text-right">Total</Th>
                    <Th className="text-right">Failed</Th>
                    <Th className="text-right">Success</Th>
                    <Th className="text-right">Avg</Th>
                    <Th>Last run</Th>
                  </tr>
                </thead>
                <tbody>
                  {workflows.data.map((row) => (
                    <tr key={row.workflow_id}>
                      <Td className="font-medium">{row.name}</Td>
                      <Td className="text-right tabular">{row.total}</Td>
                      <Td className="text-right tabular">
                        {row.failed > 0 ? <span className="text-danger">{row.failed}</span> : 0}
                      </Td>
                      <Td className="text-right tabular">
                        {row.total ? percent(row.success_rate, 0) : "—"}
                      </Td>
                      <Td className="text-right tabular text-muted-foreground">
                        {duration(row.avg_duration_ms)}
                      </Td>
                      <Td className="text-muted-foreground">{relativeTime(row.last_run_at)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Inactive learners</CardTitle>
            <Select
              aria-label="Inactivity threshold"
              value={threshold}
              onChange={(event) => setThreshold(Number(event.target.value))}
            >
              <option value={7}>7+ days</option>
              <option value={14}>14+ days</option>
              <option value={30}>30+ days</option>
            </Select>
          </CardHeader>
          <CardContent className="p-0">
            {inactive.isLoading ? (
              <div className="space-y-2 p-4">
                <Skeleton />
                <Skeleton />
              </div>
            ) : !inactive.data?.learners.length ? (
              <div className="p-4">
                <EmptyState
                  title="Everyone is active"
                  description="No learner is past the inactivity threshold."
                />
              </div>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Learner</Th>
                    <Th>Course</Th>
                    <Th className="text-right">Days idle</Th>
                    <Th className="text-right">Progress</Th>
                    <Th className="text-right">Reminders</Th>
                  </tr>
                </thead>
                <tbody>
                  {inactive.data.learners.map((learner) => (
                    <tr key={learner.id}>
                      <Td>
                        <p className="font-medium">{learner.full_name}</p>
                        <p className="text-2xs text-muted-foreground">{learner.email}</p>
                      </Td>
                      <Td className="text-muted-foreground">{learner.course_title ?? "—"}</Td>
                      <Td className="text-right tabular">{learner.days_inactive ?? "never active"}</Td>
                      <Td className="text-right tabular">{percent(learner.progress, 0)}</Td>
                      <Td className="text-right tabular">{learner.reminders_sent}</Td>
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
