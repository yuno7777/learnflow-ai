"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Modal,
  Select,
  Skeleton,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { percent } from "@/lib/format";
import type { Execution, Workflow, WorkflowStats } from "@/lib/types";

interface Template {
  name: string;
  trigger_event: string;
  description: string;
  config: Record<string, unknown>;
}

export default function WorkflowsPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [template, setTemplate] = React.useState("");
  const [key, setKey] = React.useState("");
  const [webhookPath, setWebhookPath] = React.useState("");

  const templates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: () => api.get<Record<string, Template>>("/api/v1/workflows/templates"),
  });
  const workflows = useQuery({
    queryKey: ["workflows"],
    queryFn: () => api.get<Workflow[]>("/api/v1/workflows"),
  });
  const stats = useQuery({
    queryKey: ["analytics", "workflows"],
    queryFn: () => api.get<WorkflowStats[]>("/api/v1/analytics/workflows"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Workflow>("/api/v1/workflows", {
        key: key || template.replace(/_/g, "-"),
        name: templates.data?.[template]?.name ?? template,
        template,
        n8n_webhook_path: webhookPath || null,
      }),
    onSuccess: () => {
      toast.success("Workflow created");
      setOpen(false);
      setTemplate("");
      setKey("");
      setWebhookPath("");
      void queryClient.invalidateQueries({ queryKey: ["workflows"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Create failed"),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.patch<Workflow>(`/api/v1/workflows/${id}`, { is_enabled: enabled }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["workflows"] }),
  });

  const trigger = useMutation({
    mutationFn: (id: string) =>
      api.post<Execution>(`/api/v1/workflows/${id}/trigger`, { payload: {} }),
    onSuccess: (execution) =>
      toast.success("Run queued", { description: `Execution ${execution.id.slice(0, 8)}` }),
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Trigger failed"),
  });

  const statsByWorkflow = new Map(stats.data?.map((row) => [row.workflow_id, row]));

  return (
    <>
      <PageHeader
        title="Automation workflows"
        description="Templates map onto the exported n8n workflows. Without n8n configured, the built-in runner executes the same steps."
        actions={<Button onClick={() => setOpen(true)}>New workflow</Button>}
      />

      {workflows.error ? (
        <ErrorState error={workflows.error} retry={() => workflows.refetch()} />
      ) : workflows.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !workflows.data?.length ? (
        <EmptyState
          title="No workflows yet"
          description="Create one from a template to start automating onboarding, reminders or triage."
          action={
            <Button className="mt-2" onClick={() => setOpen(true)}>
              New workflow
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Workflow</Th>
                <Th>Trigger</Th>
                <Th>Engine</Th>
                <Th className="text-right">Runs</Th>
                <Th className="text-right">Success</Th>
                <Th>Enabled</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {workflows.data.map((workflow) => {
                const row = statsByWorkflow.get(workflow.id);
                return (
                  <tr key={workflow.id} className="hover:bg-muted/50">
                    <Td>
                      <p className="font-medium">{workflow.name}</p>
                      <p className="text-2xs text-muted-foreground">{workflow.description}</p>
                    </Td>
                    <Td className="text-muted-foreground">{workflow.trigger_event ?? "manual"}</Td>
                    <Td>
                      {workflow.n8n_webhook_path ? (
                        <Badge tone="info">n8n: {workflow.n8n_webhook_path}</Badge>
                      ) : (
                        <Badge>built-in</Badge>
                      )}
                    </Td>
                    <Td className="text-right tabular">{row?.total ?? 0}</Td>
                    <Td className="text-right tabular">
                      {row?.total ? percent(row.success_rate, 0) : "—"}
                    </Td>
                    <Td>
                      <label className="flex items-center gap-2 text-2xs">
                        <input
                          type="checkbox"
                          checked={workflow.is_enabled}
                          onChange={(event) =>
                            toggle.mutate({ id: workflow.id, enabled: event.target.checked })
                          }
                        />
                        {workflow.is_enabled ? "on" : "off"}
                      </label>
                    </Td>
                    <Td className="whitespace-nowrap text-right">
                      <Button
                        variant="secondary"
                        size="sm"
                        loading={trigger.isPending && trigger.variables === workflow.id}
                        onClick={() => trigger.mutate(workflow.id)}
                      >
                        Run now
                      </Button>
                      <Link
                        href="/dashboard/executions"
                        className="ml-2 text-xs text-primary hover:underline"
                      >
                        Runs
                      </Link>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New workflow">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="template">Template</Label>
            <Select
              id="template"
              className="w-full"
              required
              value={template}
              onChange={(event) => {
                setTemplate(event.target.value);
                setKey(event.target.value.replace(/_/g, "-"));
              }}
            >
              <option value="">Select…</option>
              {Object.entries(templates.data ?? {}).map(([value, item]) => (
                <option key={value} value={value}>
                  {item.name}
                </option>
              ))}
            </Select>
            {template && (
              <p className="text-2xs text-muted-foreground">
                {templates.data?.[template]?.description}
              </p>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="key">Key</Label>
            <Input
              id="key"
              required
              pattern="[a-z0-9_-]+"
              value={key}
              onChange={(event) => setKey(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="webhook">n8n webhook path (optional)</Label>
            <Input
              id="webhook"
              placeholder="learnflow/learner-onboarding"
              value={webhookPath}
              onChange={(event) => setWebhookPath(event.target.value)}
            />
            <p className="text-2xs text-muted-foreground">
              Set this to hand execution to n8n. Leave it empty to run the built-in equivalent.
            </p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Create
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
