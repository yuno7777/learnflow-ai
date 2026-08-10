"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import * as React from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Textarea,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { AssistantReply, Me } from "@/lib/types";

const TOOL_LABELS: Record<string, string> = {
  search_knowledge_base: "Looked it up in your course material",
  get_learner_progress: "Checked your progress",
  get_course_info: "Checked your course structure",
  create_support_ticket: "Raised a support ticket for you",
  recommend_content: "Found what to study next",
};

const SUGGESTIONS = [
  "How am I doing so far?",
  "What should I study next?",
  "Explain this topic in simpler terms",
];

interface Turn {
  question: string;
  reply: AssistantReply;
}

export default function LearnerAskPage() {
  const [question, setQuestion] = React.useState("");
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [turns, setTurns] = React.useState<Turn[]>([]);

  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/auth/me") });

  const ask = useMutation({
    mutationFn: (text: string) =>
      api.post<AssistantReply>("/api/v1/assistant/ask", {
        question: text,
        // The server forces this to the caller's own learner record anyway; sending it
        // keeps the progress and recommendation tools available.
        learner_id: me.data?.learner_id ?? undefined,
        conversation_id: conversationId ?? undefined,
      }),
    onSuccess: (reply, text) => {
      setConversationId(reply.conversation_id);
      setTurns((previous) => [...previous, { question: text, reply }]);
      setQuestion("");
      if (reply.tool_calls.some((call) => call.name === "create_support_ticket")) {
        toast.info("That has been passed to a person on the support team");
      }
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Request failed"),
  });

  const send = (text: string) => {
    if (text.trim().length > 2) ask.mutate(text.trim());
  };

  return (
    <>
      <h1 className="text-xl font-semibold tracking-tight">Ask a question</h1>
      <p className="mt-1 text-xs text-muted-foreground">
        Answers come from your organization&apos;s own course material, with the sources shown.
        If the material does not cover it, you will be told rather than guessed at.
      </p>

      <div className="mt-4 space-y-4">
        {turns.length === 0 && !ask.isPending && (
          <EmptyState
            title="What would you like to know?"
            action={
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <Button key={s} variant="secondary" size="sm" onClick={() => send(s)}>
                    {s}
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
            <CardContent className="space-y-3">
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.reply.answer}</p>

              {turn.reply.tool_calls.length > 0 && (
                <ul className="flex flex-wrap gap-1.5">
                  {turn.reply.tool_calls.map((call, i) => (
                    <li key={i}>
                      <Badge>{TOOL_LABELS[call.name] ?? call.name}</Badge>
                    </li>
                  ))}
                </ul>
              )}

              {turn.reply.citations.length > 0 && (
                <div>
                  <p className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Where this came from
                  </p>
                  <ol className="mt-1.5 space-y-1.5">
                    {turn.reply.citations.map((citation, i) => (
                      <li
                        key={`${citation.chunk_id}-${i}`}
                        className="rounded-md border border-border bg-muted/40 p-2 text-2xs"
                      >
                        <span className="font-medium">{citation.document_name}</span>
                        {citation.page && (
                          <span className="text-muted-foreground"> · page {citation.page}</span>
                        )}
                        <p className="mt-1 leading-relaxed text-muted-foreground">
                          {citation.snippet.slice(0, 220)}
                          {citation.snippet.length > 220 && "…"}
                        </p>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
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
          placeholder="Ask about your course…  (⌘↵ to send)"
          className="min-h-[56px]"
          aria-label="Question"
        />
        <Button type="submit" loading={ask.isPending} disabled={question.trim().length < 3}>
          Ask
        </Button>
      </form>
    </>
  );
}
