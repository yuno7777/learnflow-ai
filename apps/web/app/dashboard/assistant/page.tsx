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
import { currency, duration, number } from "@/lib/format";
import type { KnowledgeBase, Page, RagAnswer } from "@/lib/types";

interface Turn {
  question: string;
  answer: RagAnswer;
}

export default function AssistantPage() {
  const [question, setQuestion] = React.useState("");
  const [knowledgeBaseId, setKnowledgeBaseId] = React.useState("");
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);

  const bases = useQuery({
    queryKey: ["knowledge-bases"],
    queryFn: () => api.get<Page<KnowledgeBase>>("/api/v1/knowledge-bases", { limit: 50 }),
  });

  React.useEffect(() => {
    if (!knowledgeBaseId && bases.data?.items.length) setKnowledgeBaseId(bases.data.items[0].id);
  }, [bases.data, knowledgeBaseId]);

  const ask = useMutation({
    mutationFn: (text: string) =>
      api.post<RagAnswer>("/api/v1/rag/query", {
        question: text,
        knowledge_base_id: knowledgeBaseId || undefined,
        conversation_id: conversationId ?? undefined,
      }),
    onSuccess: (answer, text) => {
      setConversationId(answer.conversation_id);
      setTurns((previous) => [...previous, { question: text, answer }]);
      setQuestion("");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Query failed"),
  });

  const rate = useMutation({
    mutationFn: ({ messageId, rating }: { messageId: string; rating: number }) =>
      api.post(
        `/api/v1/conversations/${conversationId}/messages/${messageId}/feedback`,
        { rating },
      ),
    onSuccess: () => toast.success("Feedback recorded"),
  });

  return (
    <>
      <PageHeader
        title="AI assistant"
        description="Answers are grounded in your indexed documents. If the material does not cover the question, the assistant says so instead of guessing."
        actions={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Knowledge base"
              value={knowledgeBaseId}
              onChange={(e) => setKnowledgeBaseId(e.target.value)}
            >
              <option value="">All knowledge bases</option>
              {bases.data?.items.map((kb) => (
                <option key={kb.id} value={kb.id}>
                  {kb.name}
                </option>
              ))}
            </Select>
            {conversationId && (
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
            title="Ask a question about your training material"
            description='Try something specific, like "What is the approval process for a remote work request?"'
          />
        )}

        {turns.map((turn, index) => (
          <Card key={index}>
            <CardHeader>
              <CardTitle>{turn.question}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {turn.answer.refused ? (
                <div className="rounded-md border border-warning/30 bg-warning/5 p-3">
                  <p className="text-xs font-medium text-warning">Not enough grounding</p>
                  <p className="mt-1 text-sm">{turn.answer.answer}</p>
                </div>
              ) : (
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.answer.answer}</p>
              )}

              {turn.answer.citations.length > 0 && (
                <div>
                  <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Sources
                  </p>
                  <ol className="mt-2 space-y-2">
                    {turn.answer.citations.map((citation, i) => (
                      <li
                        key={citation.chunk_id}
                        className="rounded-md border border-border bg-muted/40 p-2"
                      >
                        <div className="flex flex-wrap items-center gap-2 text-2xs">
                          <span className="font-medium">[{i + 1}]</span>
                          <span className="font-medium">{citation.document_name}</span>
                          {citation.page && (
                            <span className="text-muted-foreground">page {citation.page}</span>
                          )}
                          {citation.section && (
                            <span className="text-muted-foreground">§ {citation.section}</span>
                          )}
                          <Badge tone="info">score {citation.score.toFixed(3)}</Badge>
                        </div>
                        <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
                          {citation.snippet.slice(0, 320)}
                          {citation.snippet.length > 320 && "…"}
                        </p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3 text-2xs text-muted-foreground">
                <span>confidence {turn.answer.confidence.toFixed(3)}</span>
                <span>·</span>
                <span>{turn.answer.provider ?? "—"} / {turn.answer.model ?? "—"}</span>
                <span>·</span>
                <span>
                  {number(turn.answer.input_tokens)} in / {number(turn.answer.output_tokens)} out
                </span>
                <span>·</span>
                <span>{currency(turn.answer.estimated_cost_usd, 6)}</span>
                <span>·</span>
                <span>
                  retrieval {duration(turn.answer.retrieval_latency_ms)}, model{" "}
                  {duration(turn.answer.llm_latency_ms)}
                </span>
                {turn.answer.message_id && (
                  <span className="ml-auto flex gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        rate.mutate({ messageId: turn.answer.message_id!, rating: 1 })
                      }
                    >
                      Helpful
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        rate.mutate({ messageId: turn.answer.message_id!, rating: -1 })
                      }
                    >
                      Not helpful
                    </Button>
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <form
        className="sticky bottom-0 mt-4 flex gap-2 border-t border-border bg-background pt-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (question.trim().length > 2) ask.mutate(question.trim());
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
          placeholder="Ask about your training material…  (⌘↵ to send)"
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
