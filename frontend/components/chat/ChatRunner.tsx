"use client";

import { Bot, Download, User, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { AgentStatusTimeline } from "@/components/chat/AgentStatusTimeline";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { EditableSqlBlock } from "@/components/chat/EditableSqlBlock";
import { Markdown } from "@/components/chat/Markdown";
import { SourcePicker } from "@/components/chat/SourcePicker";
import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { TablePreview } from "@/components/chat/TablePreview";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { useTask } from "@/hooks/useTask";
import { api, HttpError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Source = { id: string; name: string };

export type RestoredMessage = {
  role: string;
  content: string;
  metadata?: Record<string, unknown>;
};

export type ActiveTask = {
  task_id: string;
  agent_run_id: string;
  prompt: string;
  status: string;
  live: boolean;
} | null;

type Turn = {
  id: string;
  userText: string;
  agentRunId: string | null;
  taskId: string | null;
  finalSteps?: import("@/hooks/useTask").StepInfo[];
  finalResult?: import("@/hooks/useTask").TaskFinalResult | null;
  finalError?: string | null;
  overrideResult?: import("@/hooks/useTask").TaskFinalResult | null;
  clarifications?: { question: string; answer: string }[];
  // Восстановленный turn после перезагрузки страницы — нет live SSE, только то
  // что лежит в БД. abandoned=true для тех, кого убил рестарт сервиса.
  restored?: boolean;
  abandoned?: boolean;
};

export function ChatRunner({
  sessionId,
  sources,
  initialMessages,
  activeTask,
}: {
  sessionId: string | null;
  sources: Source[];
  initialMessages?: RestoredMessage[];
  activeTask?: ActiveTask;
}) {
  const [turns, setTurns] = useState<Turn[]>(() => restoreTurns(initialMessages || []));
  const [sourceId, setSourceId] = useState<string | null>(sources[0]?.id ?? null);
  const [pendingClar, setPendingClar] = useState("");
  // The session this chat belongs to. On the home page the prop is null; once
  // we create a session for the first message we keep it here so every
  // following message lands in the SAME session (otherwise each message would
  // spawn a brand-new session and lose all conversation context).
  const [activeSessionId, setActiveSessionId] = useState<string | null>(sessionId);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const task = useTask();

  // Sync source after lazy fetch.
  useEffect(() => {
    if (!sourceId && sources.length > 0) {
      const first = sources[0];
      if (first) setSourceId(first.id);
    }
  }, [sources, sourceId]);

  // Restore turns when initialMessages changes (e.g. navigation between sessions).
  // If there's an active task — tack on a live turn for it and resubscribe SSE.
  useEffect(() => {
    // Сбрасываем стейт useTask + рвём прошлый SSE.
    task.reset();
    setActiveSessionId(sessionId);
    const restored = restoreTurns(initialMessages || []);
    if (activeTask && activeTask.live) {
      // Добавляем live turn для уже идущего таска и переподписываемся к SSE
      // — на бэке есть replay-буфер на 500 событий, плюс мы шлём Last-Event-ID.
      restored.push({
        id: activeTask.agent_run_id,
        userText: activeTask.prompt,
        agentRunId: activeTask.agent_run_id,
        taskId: activeTask.task_id,
        clarifications: [],
      });
      setTurns(restored);
      task.start(api.client.tasksEventsUrl(activeTask.agent_run_id));
    } else {
      setTurns(restored);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMessages, activeTask, sessionId]);

  // "Новый чат" while already on "/" is a no-op for the router (same route),
  // so the sidebar button asks us to wipe the conversation in place instead.
  useEffect(() => {
    const onNew = () => {
      task.reset();
      setTurns([]);
      setPendingClar("");
      setActiveSessionId(null);
      if (typeof window !== "undefined") {
        window.history.replaceState({}, "", "/");
      }
    };
    window.addEventListener("t2r:new-chat", onNew);
    return () => window.removeEventListener("t2r:new-chat", onNew);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tell the surrounding layout to refresh its session list whenever this chat
  // changes (new turn, finished task, etc).
  const notifySessionsChanged = useCallback(() => {
    try {
      window.dispatchEvent(new Event("t2r:sessions-changed"));
    } catch {
      /* noop */
    }
  }, []);

  // Scroll to bottom on new content.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns.length, task.steps.length, task.result, task.question, task.tokens]);

  const isRunning =
    task.state === "running" ||
    task.state === "connecting" ||
    task.state === "awaiting_input";

  const currentSource = useMemo(
    () => sources.find((s) => s.id === sourceId) ?? null,
    [sources, sourceId],
  );

  // Snapshot live state into the last turn when the task finishes.
  useEffect(() => {
    if (
      task.state !== "done" &&
      task.state !== "error" &&
      task.state !== "cancelled"
    ) {
      return;
    }
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1]!;
      if (last.finalSteps) return prev;
      return [
        ...prev.slice(0, -1),
        {
          ...last,
          finalSteps: task.steps,
          finalResult: task.result,
          finalError: task.state === "error" ? task.errorMsg : null,
        },
      ];
    });
    notifySessionsChanged();
  }, [task.state, task.steps, task.result, task.errorMsg, notifySessionsChanged]);

  const onSend = useCallback(
    async (text: string) => {
      if (!sourceId) {
        toast.error("Сначала добавьте источник данных в админке");
        return;
      }
      try {
        let sid = activeSessionId;
        if (!sid) {
          const created = await api.client.createSession({
            source_id: sourceId,
            title: text.slice(0, 80),
          });
          sid = created.id;
          setActiveSessionId(sid);
          window.history.replaceState({}, "", `/chat/${sid}`);
          notifySessionsChanged();
        }
        const { task_id, agent_run_id } = await api.client.startTask(
          sid,
          sourceId,
          text,
        );
        setTurns((prev) => [
          ...prev,
          {
            id: agent_run_id,
            userText: text,
            agentRunId: agent_run_id,
            taskId: task_id,
            clarifications: [],
          },
        ]);
        task.start(api.client.tasksEventsUrl(agent_run_id));
      } catch (err) {
        toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
      }
    },
    [sourceId, activeSessionId, task, notifySessionsChanged],
  );

  async function onRespond(answer: string) {
    const currentRunId = turns[turns.length - 1]?.agentRunId;
    if (!currentRunId) return;
    try {
      await api.client.respondTask(currentRunId, answer);
      setTurns((prev) => {
        const last = prev[prev.length - 1]!;
        return [
          ...prev.slice(0, -1),
          {
            ...last,
            clarifications: [
              ...(last.clarifications || []),
              { question: task.question || "", answer },
            ],
          },
        ];
      });
      setPendingClar("");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  async function onCancel() {
    const currentRunId = turns[turns.length - 1]?.agentRunId;
    if (!currentRunId) return;
    try {
      await api.client.cancelTask(currentRunId);
    } catch {
      /* noop */
    }
  }

  function renderTurn(t: Turn, isLast: boolean) {
    const live =
      isLast &&
      !t.restored &&
      (isRunning || task.state === "done" || task.state === "error");
    const steps = live ? task.steps : t.finalSteps || [];
    const result = t.overrideResult ?? (live ? task.result : t.finalResult || null);
    const error = live ? task.errorMsg : t.finalError;
    const preview: PreviewShape | null = hasPreview(result?.preview) ? result.preview : null;

    return (
      <div key={t.id} className="space-y-3">
        <UserMessage text={t.userText} />

        {(t.clarifications || []).map((c, i) => (
          <div key={i} className="space-y-2">
            <AssistantMessage>
              <span className="text-muted-foreground">Уточняющий вопрос:</span>{" "}
              {c.question}
            </AssistantMessage>
            <UserMessage text={c.answer} />
          </div>
        ))}

        {steps.length > 0 && (
          <div className="ml-10">
            <AgentStatusTimeline
              steps={steps}
              collapsible
              defaultCollapsed={!live && steps.length > 0}
            />
          </div>
        )}

        {isLast && task.state === "awaiting_input" && task.question && !t.restored && (
          <AssistantMessage>
            <div className="mb-2 text-muted-foreground">Уточняющий вопрос:</div>
            <div className="mb-3">{task.question}</div>
            <div className="flex gap-2">
              <input
                value={pendingClar}
                onChange={(e) => setPendingClar(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && pendingClar.trim()) {
                    onRespond(pendingClar.trim());
                  }
                }}
                placeholder="Ответ агенту…"
                className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <Button
                size="sm"
                disabled={!pendingClar.trim()}
                onClick={() => onRespond(pendingClar.trim())}
              >
                Ответить
              </Button>
            </div>
          </AssistantMessage>
        )}

        {result?.summary && (
          <AssistantMessage>
            <Markdown>{result.summary}</Markdown>
          </AssistantMessage>
        )}

        {preview && (
          <div className="ml-10 space-y-2">
            <TablePreview
              columns={preview.columns}
              rows={preview.rows}
              totalRows={preview.rows.length}
            />
            <div className="flex items-center gap-2">
              {result?.sql && t.taskId && (
                <EditableSqlBlock
                  sql={result.sql}
                  taskId={t.taskId}
                  onRerunSuccess={(r) => {
                    setTurns((prev) =>
                      prev.map((x) =>
                        x.id === t.id
                          ? {
                              ...x,
                              overrideResult: {
                                summary: `Запрос выполнен · ${r.rowcount.toLocaleString("ru-RU")} строк`,
                                sql: r.sql,
                                preview: r.preview,
                                exportUrl: r.exportUrl,
                              },
                            }
                          : x,
                      ),
                    );
                  }}
                />
              )}
              {result?.exportUrl && t.taskId && (
                <Tooltip label="Скачать полный результат в Excel">
                  <a
                    href={api.client.exportUrl(t.taskId)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-input bg-background px-3 py-1.5 text-xs transition-colors hover:border-primary/40 hover:bg-accent"
                  >
                    <Download className="h-3.5 w-3.5" /> Скачать XLSX
                  </a>
                </Tooltip>
              )}
            </div>
          </div>
        )}

        {t.abandoned && (
          <AssistantMessage error>
            <div className="font-medium">Запрос прерван перезапуском сервиса.</div>
            <div className="mt-1 text-xs">Попробуйте задать вопрос ещё раз.</div>
          </AssistantMessage>
        )}

        {error && !t.abandoned && (
          <AssistantMessage error>
            <div className="font-medium">Не получилось.</div>
            <div className="mt-1 text-xs">{error}</div>
          </AssistantMessage>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col bg-background">
      <header className="flex shrink-0 items-center justify-end gap-2 border-b bg-card/80 px-6 py-3 backdrop-blur">
        <SourcePicker
          sources={sources}
          sourceId={sourceId}
          onChange={(id) => setSourceId(id)}
        />
        {isRunning && (
          <Tooltip label="Остановить выполнение">
            <Button size="sm" variant="outline" onClick={onCancel} className="gap-1">
              <X className="h-4 w-4" /> Прервать
            </Button>
          </Tooltip>
        )}
      </header>

      <div ref={scrollRef} className="scroll-thin flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl">
          {turns.length === 0 && !isRunning ? (
            <EmptyState sourceName={currentSource?.name ?? null} hasSource={!!sourceId} />
          ) : (
            <div className="space-y-5">
              {turns.map((t, i) => renderTurn(t, i === turns.length - 1))}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0">
        <ChatComposer
          onSend={onSend}
          disabled={isRunning || !sourceId}
          placeholder={
            !sourceId
              ? "Сначала добавьте источник данных в админке…"
              : "Опишите задачу — например, «активации учителей по регионам за май»…"
          }
        />
      </div>
    </div>
  );
}

function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex animate-fade-in-up justify-end gap-2">
      <div className="max-w-[80%] rounded-2xl rounded-br-sm border border-primary/20 bg-primary/10 px-4 py-2.5 text-sm leading-relaxed text-foreground shadow-sm">
        {text}
      </div>
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <User className="h-3.5 w-3.5" />
      </div>
    </div>
  );
}

function AssistantMessage({
  children,
  error = false,
}: {
  children: React.ReactNode;
  error?: boolean;
}) {
  return (
    <div className="flex animate-fade-in-up gap-2">
      {error ? (
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <Bot className="h-3.5 w-3.5" />
        </div>
      ) : (
        <DonkeyMark size={28} rounded="rounded-full" className="mt-0.5" />
      )}
      <div
        className={cn(
          "max-w-[85%] rounded-2xl rounded-tl-sm border bg-card px-4 py-2.5 text-sm leading-relaxed shadow-sm",
          error && "border-destructive/40 bg-destructive/5 text-destructive",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function EmptyState({
  sourceName,
  hasSource,
}: {
  sourceName: string | null;
  hasSource: boolean;
}) {
  return (
    <div className="flex animate-fade-in flex-col items-center justify-center gap-5 px-4 pt-[12vh] text-center">
      <div className="relative">
        <div className="absolute inset-0 -z-10 animate-pulse-soft rounded-full bg-primary/20 blur-2xl" />
        <DonkeyMark size={64} rounded="rounded-2xl" />
      </div>
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Спросите ваши данные</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          {hasSource ? (
            <>
              Опишите задачу обычными словами — Ослик разберётся в схеме
              {sourceName ? (
                <>
                  {" "}источника <span className="font-medium text-foreground">{sourceName}</span>
                </>
              ) : null}
              , соберёт SQL, выполнит его и принесёт отчёт.
            </>
          ) : (
            "Сначала добавьте источник данных в админке — затем здесь можно будет задавать вопросы."
          )}
        </p>
      </div>
    </div>
  );
}

type PreviewShape = { columns: string[]; rows: unknown[][] };
function hasPreview(p: unknown): p is PreviewShape {
  return !!p && typeof p === "object" && "columns" in p && "rows" in p;
}

/**
 * Превращает плоский журнал chat_messages обратно в массив Turn'ов.
 * Структура: user → (опционально assistant'ы) → следующий user. Каждый assistant
 * с metadata.task_id даёт нам обратно sql/preview/exportUrl/summary; abandoned-флаг
 * показывает «прервано рестартом сервиса».
 */
function restoreTurns(messages: RestoredMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      turns.push({
        id: `restored-user-${turns.length}`,
        userText: m.content,
        agentRunId: null,
        taskId: null,
        clarifications: [],
        restored: true,
      });
      continue;
    }
    if (m.role !== "assistant") continue;
    const last = turns[turns.length - 1];
    if (!last) continue;
    const md = (m.metadata || {}) as Record<string, unknown>;
    const taskId = typeof md.task_id === "string" ? md.task_id : null;
    const sql = typeof md.sql === "string" ? md.sql : null;
    const preview = hasPreview(md.preview) ? md.preview : null;
    const summary = typeof md.summary === "string" ? md.summary : m.content;
    const exportUrl = typeof md.export_url === "string" ? md.export_url : null;
    const error = typeof md.error === "string" ? md.error : null;
    const abandoned = md.abandoned === true;
    last.taskId = taskId;
    last.finalResult = {
      summary,
      sql,
      preview: preview as unknown,
      exportUrl,
    };
    last.finalError = error;
    last.abandoned = abandoned;
  }
  return turns;
}
