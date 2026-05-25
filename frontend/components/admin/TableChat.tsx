"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useTask } from "@/hooks/useTask";
import { api, HttpError } from "@/lib/api";

type Message = {
  id: string;
  role: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

const SUGGESTIONS = [
  "Опиши таблицу более развёрнуто",
  "Какие колонки сильнее всего влияют на смысл строки?",
  "Поменяй роль customer_id на fk",
  "Добавь тег «факты»",
];

export function TableChat({
  tableId,
  onAfterApply,
}: {
  tableId: string;
  onAfterApply?: () => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingReply, setPendingReply] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const task = useTask({
    onEvent: (ev) => {
      if (ev.kind === "llm.token") {
        setPendingReply((prev) => prev + ev.chunk);
      }
    },
  });

  async function loadHistory() {
    try {
      const data = await api.tableChat.history(tableId);
      setMessages((data.messages || []) as Message[]);
    } catch (err) {
      // It's fine if there's no history yet — just log.
      console.warn("table_chat history fail", err);
    }
  }

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pendingReply]);

  useEffect(() => {
    if (task.state === "done") {
      setPendingReply("");
      loadHistory();
      onAfterApply?.();
    }
    if (task.state === "error" && task.errorMsg) {
      toast.error(task.errorMsg);
      setPendingReply("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.state]);

  async function send(prompt: string) {
    if (!prompt.trim()) return;
    setSending(true);
    setInput("");
    try {
      const { agent_run_id } = await api.tableChat.ask(tableId, prompt.trim());
      // Optimistically append the user message so it appears immediately.
      setMessages((prev) => [
        ...prev,
        {
          id: `optimistic-${Date.now()}`,
          role: "user",
          content: prompt.trim(),
          metadata: {},
          created_at: new Date().toISOString(),
        },
      ]);
      setPendingReply("");
      task.start(api.tableChat.eventsUrl(agent_run_id));
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex h-[480px] flex-col rounded-md border bg-card">
      <div className="border-b px-3 py-2 text-sm font-medium">Диалог с агентом по таблице</div>
      <div className="flex-1 overflow-auto px-3 py-2 text-sm">
        {messages.length === 0 && !pendingReply && (
          <div className="space-y-3 text-muted-foreground">
            <p>Спросите агента про эту таблицу или попросите что-то поменять.</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="rounded-full border px-3 py-1 text-xs hover:bg-muted"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="space-y-3">
          {messages.map((m) => (
            <ChatBubble key={m.id} message={m} />
          ))}
          {pendingReply && (
            <ChatBubble
              message={{
                id: "streaming",
                role: "assistant",
                content: pendingReply,
                metadata: {},
                created_at: new Date().toISOString(),
              }}
              streaming
            />
          )}
        </div>
        <div ref={bottomRef} />
      </div>
      <div className="border-t px-3 py-2">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send(input);
            }}
            placeholder="Сообщение агенту (⌘+Enter отправить)…"
            className="flex min-h-[44px] flex-1 rounded-md border bg-background px-3 py-2 text-sm"
          />
          <Button disabled={sending || !input.trim()} onClick={() => send(input)}>
            Отправить
          </Button>
        </div>
      </div>
    </div>
  );
}

function ChatBubble({
  message,
  streaming = false,
}: {
  message: Message;
  streaming?: boolean;
}) {
  const isUser = message.role === "user";
  const applied = (message.metadata?.applied as unknown[]) || [];

  const visibleContent = stripActionsBlock(message.content);

  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className={
          "max-w-[85%] rounded-lg px-3 py-2 text-sm " +
          (isUser
            ? "bg-primary/10 text-foreground"
            : "border bg-background text-foreground")
        }
      >
        <div className="whitespace-pre-wrap">{visibleContent}{streaming && <span className="ml-0.5 animate-pulse">▍</span>}</div>
        {Array.isArray(applied) && applied.length > 0 && (
          <div className="mt-2 rounded-md bg-success/10 px-2 py-1 text-xs text-success">
            ✓ Применено изменений: {applied.length}
          </div>
        )}
      </div>
    </div>
  );
}

function stripActionsBlock(s: string): string {
  return s.replace(/```json[\s\S]*?```/g, "").trim();
}
