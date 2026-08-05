"use client";

import { useQuery } from "@tanstack/react-query";
import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { AuditEntry, Page } from "@/lib/types";

export default function AuditLogPage() {
  const [action, setAction] = React.useState("");

  const logs = useQuery({
    queryKey: ["audit", action],
    queryFn: () =>
      api.get<Page<AuditEntry>>("/api/v1/audit-logs", {
        limit: 100,
        action: action || undefined,
      }),
  });

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Every privileged action, with the request id that produced it. Secret values are never recorded."
        actions={
          <Input
            placeholder="Filter by action, e.g. course.publish"
            className="w-72"
            value={action}
            onChange={(event) => setAction(event.target.value)}
            aria-label="Filter by action"
          />
        }
      />

      {logs.error ? (
        <ErrorState error={logs.error} retry={() => logs.refetch()} />
      ) : logs.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !logs.data?.items.length ? (
        <EmptyState
          title="No audit entries"
          description={action ? "No entries match that action." : "Actions appear here as they happen."}
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Actor</Th>
                <Th>Action</Th>
                <Th>Resource</Th>
                <Th>Request id</Th>
                <Th>Details</Th>
              </tr>
            </thead>
            <tbody>
              {logs.data.items.map((entry) => (
                <tr key={entry.id}>
                  <Td className="whitespace-nowrap text-muted-foreground">
                    {dateTime(entry.created_at)}
                  </Td>
                  <Td>
                    <p className="text-xs">{entry.actor_label ?? "system"}</p>
                    <Badge>{entry.actor_type}</Badge>
                  </Td>
                  <Td className="font-medium">{entry.action}</Td>
                  <Td className="text-2xs text-muted-foreground">
                    {entry.resource_type}
                    {entry.resource_id ? ` · ${entry.resource_id.slice(0, 8)}` : ""}
                  </Td>
                  <Td className="font-mono text-2xs text-muted-foreground">
                    {entry.request_id?.slice(0, 12) ?? "—"}
                  </Td>
                  <Td className="max-w-sm truncate text-2xs text-muted-foreground">
                    {Object.keys(entry.details).length ? JSON.stringify(entry.details) : "—"}
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
