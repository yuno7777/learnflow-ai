"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
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
import { compactNumber, currency, duration, number } from "@/lib/format";
import type { UsageSummary } from "@/lib/types";

const TOOLTIP = {
  background: "hsl(var(--card))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 6,
  fontSize: 12,
};

export default function UsagePage() {
  const [days, setDays] = React.useState(30);

  const usage = useQuery({
    queryKey: ["usage", days],
    queryFn: () => api.get<UsageSummary>("/api/v1/analytics/usage", { days }),
  });

  const data = usage.data;

  return (
    <>
      <PageHeader
        title="AI usage and cost"
        description="Estimated from recorded token counts and a per-model price table. Estimates, not an invoice."
        actions={
          <Select
            aria-label="Time range"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </Select>
        }
      />

      {usage.error ? (
        <ErrorState error={usage.error} retry={() => usage.refetch()} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric
              label="Requests"
              value={compactNumber(data?.total_requests)}
              loading={usage.isLoading}
            />
            <Metric
              label="Input tokens"
              value={compactNumber(data?.total_input_tokens)}
              loading={usage.isLoading}
            />
            <Metric
              label="Output tokens"
              value={compactNumber(data?.total_output_tokens)}
              loading={usage.isLoading}
            />
            <Metric
              label="Estimated cost"
              value={currency(data?.total_cost_usd)}
              loading={usage.isLoading}
            />
          </div>

          <Card className="mt-4">
            <CardHeader>
              <CardTitle>Daily cost and tokens</CardTitle>
            </CardHeader>
            <CardContent>
              {usage.isLoading ? (
                <Skeleton className="h-56" />
              ) : !data?.daily.length ? (
                <EmptyState title="No usage recorded in this period" />
              ) : (
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data.daily} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" vertical={false} />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10 }}
                        stroke="hsl(var(--muted-foreground))"
                        tickFormatter={(value: string) => value.slice(5)}
                      />
                      <YAxis yAxisId="cost" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
                      <YAxis
                        yAxisId="tokens"
                        orientation="right"
                        tick={{ fontSize: 10 }}
                        stroke="hsl(var(--muted-foreground))"
                      />
                      <Tooltip contentStyle={TOOLTIP} />
                      <Line
                        yAxisId="cost"
                        type="monotone"
                        dataKey="value"
                        name="cost (USD)"
                        stroke="hsl(var(--primary))"
                        strokeWidth={2}
                        dot={false}
                      />
                      <Line
                        yAxisId="tokens"
                        type="monotone"
                        dataKey="secondary"
                        name="tokens"
                        stroke="hsl(var(--warning))"
                        strokeWidth={1.5}
                        dot={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </CardContent>
          </Card>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Cost by model</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {usage.isLoading ? (
                  <div className="space-y-2 p-4">
                    <Skeleton />
                    <Skeleton />
                  </div>
                ) : !data?.by_model.length ? (
                  <div className="p-4">
                    <EmptyState title="No model usage yet" />
                  </div>
                ) : (
                  <Table>
                    <thead>
                      <tr>
                        <Th>Provider / model</Th>
                        <Th className="text-right">Requests</Th>
                        <Th className="text-right">Tokens</Th>
                        <Th className="text-right">Cost</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_model.map((row) => (
                        <tr key={`${row.provider}-${row.model}`}>
                          <Td>
                            <p className="font-medium">{row.model}</p>
                            <p className="text-2xs text-muted-foreground">{row.provider}</p>
                          </Td>
                          <Td className="text-right tabular">{number(row.requests)}</Td>
                          <Td className="text-right tabular">
                            {compactNumber(row.input_tokens + row.output_tokens)}
                          </Td>
                          <Td className="text-right tabular">{currency(row.estimated_cost_usd)}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Requests by operation</CardTitle>
              </CardHeader>
              <CardContent>
                {usage.isLoading ? (
                  <Skeleton className="h-56" />
                ) : !data?.by_operation.length ? (
                  <EmptyState title="No operations recorded" />
                ) : (
                  <div className="h-56">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={data.by_operation}
                        layout="vertical"
                        margin={{ top: 4, right: 8, bottom: 0, left: 40 }}
                      >
                        <CartesianGrid stroke="hsl(var(--border))" strokeDasharray="3 3" horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 10 }} stroke="hsl(var(--muted-foreground))" />
                        <YAxis
                          type="category"
                          dataKey="operation"
                          width={130}
                          tick={{ fontSize: 10 }}
                          stroke="hsl(var(--muted-foreground))"
                        />
                        <Tooltip contentStyle={TOOLTIP} />
                        <Bar dataKey="requests" name="requests" fill="hsl(var(--primary))" radius={[0, 3, 3, 0]} />
                        <Bar dataKey="failures" name="failures" fill="hsl(var(--danger))" radius={[0, 3, 3, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader>
              <CardTitle>Operation detail</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <thead>
                  <tr>
                    <Th>Operation</Th>
                    <Th className="text-right">Requests</Th>
                    <Th className="text-right">Failures</Th>
                    <Th className="text-right">Tokens</Th>
                    <Th className="text-right">Avg latency</Th>
                    <Th className="text-right">Cost</Th>
                  </tr>
                </thead>
                <tbody>
                  {data?.by_operation.map((row) => (
                    <tr key={row.operation}>
                      <Td className="font-medium">{row.operation}</Td>
                      <Td className="text-right tabular">{number(row.requests)}</Td>
                      <Td className="text-right tabular">
                        {row.failures > 0 ? (
                          <span className="text-danger">{row.failures}</span>
                        ) : (
                          0
                        )}
                      </Td>
                      <Td className="text-right tabular">{compactNumber(row.tokens)}</Td>
                      <Td className="text-right tabular text-muted-foreground">
                        {duration(row.avg_latency_ms)}
                      </Td>
                      <Td className="text-right tabular">{currency(row.estimated_cost_usd)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
