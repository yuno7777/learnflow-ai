"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
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
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Job, Page } from "@/lib/types";

const KINDS = [
  "",
  "ingest_document",
  "generate_course",
  "regenerate_module",
  "generate_assessment",
  "evaluate_submission",
  "run_workflow",
  "transform_content",
];

export default function JobsPage() {
  const [kind, setKind] = React.useState("");

  const jobs = useQuery({
    queryKey: ["jobs", kind],
    queryFn: () => api.get<Page<Job>>("/api/v1/jobs", { limit: 50, kind: kind || undefined }),
    refetchInterval: (query) =>
      query.state.data?.items.some((job) => job.status === "queued" || job.status === "running")
        ? 3000
        : false,
  });

  return (
    <>
      <PageHeader
        title="Background jobs"
        description="Every async unit of work has a ledger row with an idempotency key, attempt count and retry policy."
        actions={
          <Select aria-label="Job kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {value === "" ? "All kinds" : value.replace(/_/g, " ")}
              </option>
            ))}
          </Select>
        }
      />

      {jobs.error ? (
        <ErrorState error={jobs.error} retry={() => jobs.refetch()} />
      ) : jobs.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !jobs.data?.items.length ? (
        <EmptyState
          title="No jobs"
          description="Uploading a document or generating a course queues work here."
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Kind</Th>
                <Th>Status</Th>
                <Th className="w-40">Progress</Th>
                <Th className="text-right">Attempts</Th>
                <Th>Resource</Th>
                <Th>Message / error</Th>
                <Th>Queued</Th>
              </tr>
            </thead>
            <tbody>
              {jobs.data.items.map((job) => (
                <tr key={job.id}>
                  <Td className="font-medium">{job.kind.replace(/_/g, " ")}</Td>
                  <Td>
                    <StatusBadge status={job.status} />
                  </Td>
                  <Td>
                    <Progress value={job.progress} label={`${job.kind} progress`} />
                  </Td>
                  <Td className="text-right tabular">
                    {job.attempts}/{job.max_attempts}
                  </Td>
                  <Td className="text-2xs text-muted-foreground">
                    {job.resource_type}
                    {job.resource_id ? ` · ${job.resource_id.slice(0, 8)}` : ""}
                  </Td>
                  <Td className="max-w-sm truncate text-2xs">
                    {job.error ? (
                      <span className="text-danger">{job.error}</span>
                    ) : (
                      <span className="text-muted-foreground">{job.message ?? "—"}</span>
                    )}
                  </Td>
                  <Td className="text-muted-foreground">{dateTime(job.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
    </>
  );
}
