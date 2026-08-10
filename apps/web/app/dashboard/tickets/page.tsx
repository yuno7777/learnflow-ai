"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import { Pagination } from "@/components/pagination";
import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Drawer,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Modal,
  Select,
  Skeleton,
  StatusBadge,
  Table,
  Td,
  Textarea,
  Th,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { Page, Ticket } from "@/lib/types";

const CATEGORIES = [
  "",
  "course_access",
  "payment_issue",
  "technical_problem",
  "assessment_query",
  "certificate_issue",
  "general_learning",
];

export default function TicketsPage() {
  const queryClient = useQueryClient();
  const [offset, setOffset] = React.useState(0);
  const [limit, setLimit] = React.useState(25);
  const [category, setCategory] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [selected, setSelected] = React.useState<Ticket | null>(null);
  const [form, setForm] = React.useState({ subject: "", body: "" });

  const tickets = useQuery({
    queryKey: ["tickets", category, offset, limit],
    queryFn: () =>
      api.get<Page<Ticket>>("/api/v1/tickets", { limit,
        offset, category: category || undefined }),
    refetchInterval: 20_000,
  });

  const create = useMutation({
    mutationFn: () => api.post<Ticket>("/api/v1/tickets", { ...form, triage: true }),
    onSuccess: () => {
      toast.success("Ticket raised", { description: "AI triage runs in the background." });
      setOpen(false);
      setForm({ subject: "", body: "" });
      void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not raise ticket"),
  });

  const update = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch<Ticket>(`/api/v1/tickets/${id}`, { status }),
    onSuccess: () => {
      toast.success("Ticket updated");
      setSelected(null);
      void queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
  });

  return (
    <>
      <PageHeader
        title="Support tickets"
        description="Triage classifies, scores urgency and drafts a knowledge-base grounded reply. Low confidence escalates instead of guessing."
        actions={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Category filter"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              {CATEGORIES.map((value) => (
                <option key={value} value={value}>
                  {value === "" ? "All categories" : value.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
            <Button onClick={() => setOpen(true)}>New ticket</Button>
          </div>
        }
      />

      {tickets.error ? (
        <ErrorState error={tickets.error} retry={() => tickets.refetch()} />
      ) : tickets.isLoading ? (
        <Card>
          <CardContent className="space-y-2">
            <Skeleton />
            <Skeleton />
          </CardContent>
        </Card>
      ) : !tickets.data?.items.length ? (
        <EmptyState title="No tickets" description="Raise one to watch the triage workflow run." />
      ) : (
        <Card>
          <Table>
            <thead>
              <tr>
                <Th>Subject</Th>
                <Th>Status</Th>
                <Th>Category</Th>
                <Th>Urgency</Th>
                <Th>Sentiment</Th>
                <Th className="text-right">AI confidence</Th>
                <Th>Raised</Th>
              </tr>
            </thead>
            <tbody>
              {tickets.data.items.map((ticket) => (
                <tr
                  key={ticket.id}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setSelected(ticket)}
                >
                  <Td>
                    <p className="font-medium">{ticket.subject}</p>
                    <p className="max-w-md truncate text-2xs text-muted-foreground">{ticket.body}</p>
                  </Td>
                  <Td>
                    <StatusBadge status={ticket.status} />
                  </Td>
                  <Td className="text-muted-foreground">
                    {ticket.category?.replace(/_/g, " ") ?? "—"}
                  </Td>
                  <Td>
                    {ticket.urgency ? (
                      <Badge
                        tone={
                          ticket.urgency === "critical" || ticket.urgency === "high"
                            ? "danger"
                            : "neutral"
                        }
                      >
                        {ticket.urgency}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </Td>
                  <Td className="text-muted-foreground">{ticket.sentiment ?? "—"}</Td>
                  <Td className="text-right tabular">
                    {ticket.ai_confidence?.toFixed(2) ?? "—"}
                  </Td>
                  <Td className="text-muted-foreground">{dateTime(ticket.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination
            total={tickets.data?.total ?? 0}
            limit={limit}
            offset={offset}
            onOffsetChange={setOffset}
            onLimitChange={setLimit}
            label="tickets"
          />
        </Card>
      )}

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.subject ?? ""}
      >
        {selected && (
          <div className="space-y-4">
            <p className="whitespace-pre-wrap text-sm">{selected.body}</p>

            <div className="flex flex-wrap gap-2">
              <StatusBadge status={selected.status} />
              {selected.category && <Badge tone="info">{selected.category.replace(/_/g, " ")}</Badge>}
              {selected.escalated && <Badge tone="danger">escalated</Badge>}
            </div>

            {selected.ai_explanation && (
              <Section title="AI decision">
                <p className="text-xs text-muted-foreground">{selected.ai_explanation}</p>
              </Section>
            )}

            {selected.suggested_response && (
              <Section title="Suggested response">
                <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                  {selected.suggested_response}
                </p>
              </Section>
            )}

            {selected.kb_references.length > 0 && (
              <Section title="Knowledge base references">
                <ul className="space-y-1">
                  {selected.kb_references.map((reference, index) => (
                    <li key={index} className="text-2xs text-muted-foreground">
                      • {String((reference as { document?: string }).document ?? "source")} (score{" "}
                      {String((reference as { score?: number }).score ?? "—")})
                    </li>
                  ))}
                </ul>
              </Section>
            )}

            <div className="flex gap-2 border-t border-border pt-3">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => update.mutate({ id: selected.id, status: "in_progress" })}
              >
                Mark in progress
              </Button>
              <Button size="sm" onClick={() => update.mutate({ id: selected.id, status: "resolved" })}>
                Resolve
              </Button>
            </div>
          </div>
        )}
      </Drawer>

      <Modal open={open} onClose={() => setOpen(false)} title="New support ticket">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="ticket-subject">Subject</Label>
            <Input
              id="ticket-subject"
              required
              minLength={3}
              value={form.subject}
              onChange={(event) => setForm({ ...form, subject: event.target.value })}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="ticket-body">Description</Label>
            <Textarea
              id="ticket-body"
              required
              minLength={5}
              className="min-h-[120px]"
              value={form.body}
              onChange={(event) => setForm({ ...form, body: event.target.value })}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Raise ticket
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      <div className="mt-1">{children}</div>
    </div>
  );
}
