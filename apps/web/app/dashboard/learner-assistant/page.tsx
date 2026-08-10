"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
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
  EmptyState,
  Select,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { currency, number } from "@/lib/format";
import type { AssistantReply, Learner, Page } from "@/lib/types";

const TOOL_LABELS: Record<string, string> = {
  search_knowledge_base: "Searched the knowledge base",
  get_learner_progress: "Read learner progress",
  get_course_info: "Read course structure",
  create_support_ticket: "Raised a support ticket",
  recommend_content: "Recommended next lessons",
};

const SUGGESTIONS = [
  "Explain the remote work approval process in simple terms",
  "How am I doing on my course so far?",
  "What should I study next?",
  "I cannot open module 2 and I have a deadline",
];

interface Turn {
  question: string;
  reply: AssistantReply;
}

export default function LearnerAssistantPage() {
  const [learnerId, setLearnerId] = React.useState("");
  const [question, setQuestion] = React.useState("");
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);

  const learners = useQuery({
    queryKey: ["learners", "assistant"],
    queryFn: () => api.get<Page<Learner>>("/api/v1/learners", { limit: 100 }),
  });

  React.useEffect(() => {
    if (!learnerId && learners.data?.items.length) setLearnerId(learners.data.items[0].id);
  }, [learners.data, learnerId]);

  const ask = useMutation({
    mutationFn: (text: string) =>
      api.post<AssistantReply>("/api/v1/assistant/ask", {
        question: text,
        learner_id: learnerId || undefined,
        conversation_id: conversationId ?? undefined,
      }),
    onSuccess: (reply, text) => {
      setConversationId(reply.conversation_id);
      setTurns((previous) => [...previous, { question: text, reply }]);
      setQuestion("");
      if (reply.tool_calls.some((call) => call.name === "create_support_ticket")) {
        toast.info("The assistant escalated this to a support ticket");
      }
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Request failed"),
  });

  function send(text: string) {
    if (text.trim().length > 2) ask.mutate(text.trim());
  }

  return (
    <>
      <PageHeader
        title="Learner assistant"
        description="The learner-facing assistant. It searches the knowledge base, reads the learner's own progress and course structure, recommends what to study next, and escalates to a support ticket when the material cannot answer the question."
        actions={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Acting as learner"
              value={learnerId}
              onChange={(event) => {
                setLearnerId(event.target.value);
                setConversationId(null);
                setTurns([]);
              }}
            >
              <option value="">No learner context</option>
              {learners.data?.items.map((learner) => (
                <option key={learner.id} value={learner.id}>
                  {learner.full_name}
                </option>
              ))}
            </Select>
            {turns.length > 0 && (
              <Button
                variant="secondary"
                onClick={() => {
                  setConversationId(null);
                  setTurns([]);
                }}
              >
                New conversation
              </Button>
            )}
          </div>
        }
      />

      <div className="space-y-4">
        {turns.length === 0 && !ask.isPending && (
          <EmptyState
            title="Ask as a learner"
            description="Progress and recommendation tools only work when a learner is selected above."
            action={
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <Button
                    key={suggestion}
                    variant="secondary"
                    size="sm"
                    onClick={() => send(suggestion)}
                  >
                    {suggestion}
                  </Button>
                ))}
              </div>
            }
          />
        )}

        {turns.map((turn, index) => (
          <Card key={index}>
            <CardHeader>
              <CardTitle>{turn.question}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.reply.answer}</p>

              {turn.reply.tool_calls.length > 0 && (
                <div>
                  <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Tools used
                  </p>
                  <ol className="mt-2 space-y-1">
                    {turn.reply.tool_calls.map((call, i) => (
                      <li key={i} className="flex items-center gap-2 text-2xs">
                        <Badge tone="info">{i + 1}</Badge>
                        <span>{TOOL_LABELS[call.name] ?? call.name}</span>
                        {Object.keys(call.arguments ?? {}).length > 0 && (
                          <code className="truncate rounded bg-muted px-1 py-0.5 text-muted-foreground">
                            {JSON.stringify(call.arguments)}
                          </code>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {turn.reply.citations.length > 0 && (
                <div>
                  <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Sources
                  </p>
                  <ol className="mt-2 space-y-2">
                    {turn.reply.citations.map((citation, i) => (
                      <li
                        key={`${citation.chunk_id}-${i}`}
                        className="rounded-md border border-border bg-muted/40 p-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-2xs">
                          <span className="font-medium">[{i + 1}]</span>
                          <span className="font-medium">{citation.document_name}</span>
                          {citation.page && (
                            <span className="text-muted-foreground">page {citation.page}</span>
                          )}
                          <Badge tone="info">score {citation.score.toFixed(3)}</Badge>
                        </div>
                        <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
                          {citation.snippet.slice(0, 260)}
                          {citation.snippet.length > 260 && "…"}
                        </p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3 text-2xs text-muted-foreground">
                <span>{turn.reply.model}</span>
                <span>·</span>
                <span>
                  {number(turn.reply.input_tokens)} in / {number(turn.reply.output_tokens)} out
                </span>
                <span>·</span>
                <span>{currency(turn.reply.estimated_cost_usd, 6)}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <form
        className="sticky bottom-0 mt-4 flex gap-2 border-t border-border bg-background pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          send(question);
        }}
      >
        <Textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Ask as this learner…  (⌘↵ to send)"
          className="min-h-[60px]"
          aria-label="Question"
        />
        <Button type="submit" loading={ask.isPending} disabled={question.trim().length < 3}>
          Ask
        </Button>
      </form>
    </>
  );
}
