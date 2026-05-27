"use client";

import { ArrowDown, Bot, Check, ListChecks, Pencil, Sparkles, User, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { toast } from "sonner";

import { AgentStatusTimeline } from "@/components/chat/AgentStatusTimeline";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { EditableSqlBlock } from "@/components/chat/EditableSqlBlock";
import { Markdown } from "@/components/chat/Markdown";
import { SourcePicker } from "@/components/chat/SourcePicker";
import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
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
  const clarInputRef = useRef<HTMLInputElement>(null);
  // The session this chat belongs to. On the home page the prop is null; once
  // we create a session for the first message we keep it here so every
  // following message lands in the SAME session (otherwise each message would
  // spawn a brand-new session and lose all conversation context).
  const [activeSessionId, setActiveSessionId] = useState<string | null>(sessionId);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Whether the view is "pinned" to the bottom. Stays true while the user reads
  // the latest content; flips to false the moment they scroll up to read
  // history, so streaming updates no longer yank them back down.
  const stickRef = useRef(true);
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
    stickRef.current = true; // открыли сессию — показываем низ переписки
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
      stickRef.current = true;
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

  // Track whether the user is reading the latest content (near the bottom) or
  // has scrolled up into history. Only re-pin when they're near the bottom.
  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  }, []);

  // Keep pinned to the bottom on new content — but only if the user hasn't
  // scrolled away. Uses "auto" (instant) so a burst of streaming step events
  // follows the growing content like a terminal instead of stutter-animating.
  useEffect(() => {
    if (!stickRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    const id = requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight });
    });
    return () => cancelAnimationFrame(id);
  }, [turns.length, task.steps.length, task.result, task.question, task.state]);

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
      stickRef.current = true; // пользователь отправил — следуем за ответом
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
    stickRef.current = true;
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
    // The agent's prose answer is fixed once produced. Re-running the SQL only
    // refreshes the DATA (table/sql/export) via overrideResult — it must never
    // clobber the answer bubble. So keep the two separate.
    const baseResult = live ? task.result : t.finalResult || null;
    const answer = baseResult?.summary;
    const data = t.overrideResult ?? baseResult;
    const error = live ? task.errorMsg : t.finalError;
    const preview: PreviewShape | null = hasPreview(data?.preview) ? data.preview : null;

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

        {isLast && task.state === "awaiting_input" && task.question && !t.restored &&
          (task.choices?.length ? (
            <PlanConfirmCard
              question={task.question}
              affirmative={task.choices.find((c) => /^да/i.test(c)) ?? task.choices[0]!}
              correction={pendingClar}
              setCorrection={setPendingClar}
              inputRef={clarInputRef}
              onAffirm={onRespond}
              onCorrect={onRespond}
            />
          ) : (
            <AssistantMessage>
              <div className="mb-1.5 text-muted-foreground">Уточняющий вопрос:</div>
              <div className="mb-3">
                <Markdown>{task.question}</Markdown>
              </div>
              <div className="flex gap-2">
                <input
                  value={pendingClar}
                  onChange={(e) => setPendingClar(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && pendingClar.trim()) onRespond(pendingClar.trim());
                  }}
                  placeholder="Ответ агенту…"
                  className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <Button size="sm" disabled={!pendingClar.trim()} onClick={() => onRespond(pendingClar.trim())}>
                  Ответить
                </Button>
              </div>
            </AssistantMessage>
          ))}

        {answer && (
          <AssistantMessage prominent>
            <Markdown>{answer}</Markdown>
          </AssistantMessage>
        )}

        {preview && (
          <div className="ml-10 space-y-2">
            <TablePreview
              columns={preview.columns}
              rows={preview.rows}
              totalRows={preview.rows.length}
              exportHref={data?.exportUrl && t.taskId ? api.client.exportUrl(t.taskId) : null}
            />
            {data?.sql && t.taskId && (
              <EditableSqlBlock
                sql={data.sql}
                taskId={t.taskId}
                sourceName={currentSource?.name}
                onRerunSuccess={(r) => {
                  setTurns((prev) =>
                    prev.map((x) =>
                      x.id === t.id
                        ? {
                            ...x,
                            // Only the data is replaced; `answer` stays bound to
                            // baseResult.summary so the prose bubble is untouched.
                            overrideResult: {
                              summary: x.finalResult?.summary ?? null,
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
      {/* Left side stays clear: when the sidebar is collapsed the layout's
          «сессии» pill sits there. Running state is shown by the timeline and
          the Прервать button. */}
      <header className="flex shrink-0 items-center justify-end gap-2 border-b bg-card/80 px-4 py-3 backdrop-blur sm:px-6">
        <ThemeToggle />
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

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="scroll-thin h-full overflow-y-auto px-4 py-6"
        >
          <div className="mx-auto max-w-[var(--chat-max,48rem)] transition-[max-width] duration-300 ease-in-out">
            {turns.length === 0 && !isRunning ? (
              <EmptyState
                sourceName={currentSource?.name ?? null}
                hasSource={!!sourceId}
                onPick={onSend}
              />
            ) : (
              <div className="space-y-5">
                {turns.map((t, i) => renderTurn(t, i === turns.length - 1))}
              </div>
            )}
          </div>
        </div>

        <ScrollToBottom
          containerRef={scrollRef}
          deps={[turns.length, task.steps.length, task.state]}
          onClick={() => {
            stickRef.current = true;
            scrollRef.current?.scrollTo({
              top: scrollRef.current.scrollHeight,
              behavior: "smooth",
            });
          }}
        />
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

// The "сверим задачу" panel: the agent restates the task in plain language and
// waits for a yes / correction before writing SQL. Distinct from a plain
// clarification — a framed, primary-tinted card so it reads as a checkpoint.
function PlanConfirmCard({
  question,
  affirmative,
  correction,
  setCorrection,
  inputRef,
  onAffirm,
  onCorrect,
}: {
  question: string;
  affirmative: string;
  correction: string;
  setCorrection: (v: string) => void;
  inputRef: RefObject<HTMLInputElement>;
  onAffirm: (answer: string) => void;
  onCorrect: (answer: string) => void;
}) {
  return (
    <div className="ml-10 animate-fade-in-up">
      <div className="overflow-hidden rounded-lg border border-primary/30 bg-primary/[0.03] shadow-sm">
        <div className="flex items-center gap-2 border-b border-primary/15 bg-primary/[0.06] px-4 py-2">
          <ListChecks className="h-3.5 w-3.5 text-primary" />
          <span className="label-mono text-primary/80">сверим задачу перед расчётом</span>
        </div>
        <div className="px-4 py-3">
          <Markdown>{question}</Markdown>
        </div>
        <div className="space-y-2.5 border-t border-primary/15 bg-muted/20 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" className="gap-1.5" onClick={() => onAffirm(affirmative)}>
              <Check className="h-3.5 w-3.5" />
              {affirmative}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={() => inputRef.current?.focus()}
            >
              <Pencil className="h-3.5 w-3.5" />
              Нет — поправлю
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && correction.trim()) onCorrect(correction.trim());
              }}
              placeholder="или сразу напишите, что поправить…"
              className="min-w-[160px] flex-1 rounded-md border bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button
              size="sm"
              variant="ghost"
              disabled={!correction.trim()}
              onClick={() => correction.trim() && onCorrect(correction.trim())}
            >
              Отправить
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function UserMessage({ text }: { text: string }) {
  return (
    <div className="flex animate-fade-in-up justify-end gap-2">
      <div className="flex max-w-[80%] flex-col items-end gap-1">
        <span className="label-mono px-0.5 text-primary/70">вы</span>
        <div className="rounded-md rounded-tr-none border border-primary/25 bg-primary/10 px-3.5 py-2 text-sm leading-relaxed text-foreground">
          {text}
        </div>
      </div>
      <div className="mt-[1.55rem] flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-muted text-muted-foreground">
        <User className="h-3.5 w-3.5" />
      </div>
    </div>
  );
}

function AssistantMessage({
  children,
  error = false,
  prominent = false,
}: {
  children: React.ReactNode;
  error?: boolean;
  // The final answer is the hero of a turn: bigger prose + a left accent rule so
  // the eye lands on it, with the timeline/table/SQL reading as supporting evidence.
  prominent?: boolean;
}) {
  return (
    <div className="flex animate-fade-in-up gap-2">
      {error ? (
        <div className="mt-[1.55rem] flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-destructive/30 bg-destructive/10 text-destructive">
          <Bot className="h-3.5 w-3.5" />
        </div>
      ) : (
        <DonkeyMark size={28} rounded="rounded-md" className="mt-[1.55rem]" />
      )}
      <div className="flex min-w-0 max-w-[85%] flex-col gap-1">
        <span
          className={cn(
            "label-mono px-0.5",
            error ? "text-destructive/80" : prominent ? "text-primary/80" : "text-muted-foreground",
          )}
        >
          {error ? "ошибка" : prominent ? "ответ" : "ослик"}
        </span>
        <div
          className={cn(
            "rounded-md rounded-tl-none border bg-card px-3.5 py-2 text-sm leading-relaxed shadow-sm",
            prominent &&
              "border-l-2 border-l-primary/60 bg-card px-4 py-3 text-[15px] leading-relaxed shadow",
            error && "border-destructive/40 bg-destructive/5 text-destructive",
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
}

const EXAMPLE_PROMPTS = [
  "Сколько активных учителей сейчас?",
  "Динамика активаций учителей по месяцам",
  "Топ-10 школ по числу учителей",
  "Сравни активность учителей по регионам",
];

function EmptyState({
  sourceName,
  hasSource,
  onPick,
}: {
  sourceName: string | null;
  hasSource: boolean;
  onPick: (text: string) => void;
}) {
  return (
    <div className="relative">
      <div className="terminal-grid terminal-grid-fade pointer-events-none absolute inset-x-0 top-0 -z-10 h-[60vh]" />
      <div className="flex animate-fade-in flex-col items-center justify-center gap-6 px-4 pt-[10vh] text-center">
        <div className="relative">
          <div className="absolute inset-0 -z-10 animate-pulse-soft rounded-2xl bg-primary/20 blur-2xl" />
          <DonkeyMark size={64} rounded="rounded-lg" />
        </div>

        <div className="space-y-3">
          <span className="label-mono inline-block">аналитический ослик · v1</span>
          <h1 className="caret text-2xl font-semibold tracking-tight">Спросите ваши данные</h1>
          <p className="mx-auto max-w-md font-sans text-sm leading-relaxed text-muted-foreground">
            {hasSource ? (
              <>
                Опишите задачу обычными словами — Ослик разберётся в схеме
                {sourceName ? (
                  <>
                    {" "}источника <span className="font-mono text-foreground">{sourceName}</span>
                  </>
                ) : null}
                , соберёт SQL, выполнит его и принесёт отчёт.
              </>
            ) : (
              "Сначала добавьте источник данных в админке — затем здесь можно будет задавать вопросы."
            )}
          </p>
        </div>

        {hasSource && (
          <div className="w-full max-w-md space-y-2 text-left">
            <span className="label-mono block px-1">попробуйте</span>
            <div className="flex flex-col gap-1.5">
              {EXAMPLE_PROMPTS.map((p, i) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => onPick(p)}
                  style={{ animationDelay: `${100 + i * 55}ms` }}
                  className="group flex animate-fade-in-up items-center gap-2.5 rounded-md border bg-card px-3 py-2 text-left text-sm text-muted-foreground transition-all hover:border-primary/40 hover:bg-primary/[0.04] hover:text-foreground"
                >
                  <span className="font-mono text-primary/50 transition-colors group-hover:text-primary">
                    &gt;
                  </span>
                  <span className="min-w-0 flex-1 truncate">{p}</span>
                  <Sparkles className="h-3.5 w-3.5 shrink-0 text-transparent transition-colors group-hover:text-primary/70" />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Floating "scroll to latest" pill. Shows only when the scroll container is
 * meaningfully above the bottom — so once the user reads history, they always
 * have a one-tap way back to the live answer without us hijacking their scroll.
 */
function ScrollToBottom({
  containerRef,
  deps,
  onClick,
}: {
  containerRef: RefObject<HTMLDivElement>;
  deps: unknown[];
  onClick: () => void;
}) {
  const [show, setShow] = useState(false);

  const check = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    setShow(el.scrollHeight - el.scrollTop - el.clientHeight > 240);
  }, [containerRef]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener("scroll", check, { passive: true });
    check();
    return () => el.removeEventListener("scroll", check);
  }, [containerRef, check]);

  // Re-evaluate when content grows (new turn / streaming step).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(check, deps);

  if (!show) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="К последнему сообщению"
      className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 animate-fade-in-up items-center gap-1.5 rounded-full border bg-card/90 px-3 py-1.5 text-xs font-medium text-foreground shadow-lg backdrop-blur transition-all hover:border-primary/40 hover:bg-card"
    >
      <ArrowDown className="h-3.5 w-3.5 text-primary" /> К ответу
    </button>
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
