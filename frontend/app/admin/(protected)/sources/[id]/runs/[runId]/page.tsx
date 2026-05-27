"use client";

import { CheckCircle2, ChevronLeft, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { ColumnSelectionGate } from "@/components/admin/ColumnSelectionGate";
import { QuestionInbox } from "@/components/admin/QuestionInbox";
import { TaskBoard } from "@/components/admin/TaskBoard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError, type ProfilingProgress } from "@/lib/api";

const ACTIVE = new Set(["pending", "running", "paused", "awaiting_input"]);

export default function ProfilingRunPage() {
  const params = useParams<{ id: string; runId: string }>();
  const runId = params.runId;
  const [progress, setProgress] = useState<ProfilingProgress | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setProgress(await api.profiling.progress(runId));
    } catch {
      /* transient — keep last */
    }
  }, [runId]);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 1500);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [refresh]);

  async function onAnswer(
    taskId: string,
    answers: { column?: string | null; text?: string | null; answer: string }[],
  ) {
    try {
      await api.profiling.answerTask(taskId, answers);
      toast.success("Ответ принят — продолжаю профилирование");
      await refresh();
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  async function onCancel() {
    if (!progress?.agent_run_id) return;
    try {
      await api.profiling.cancel(progress.agent_run_id);
      toast.message("Запрос на прерывание отправлен");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Не удалось прервать");
    }
  }

  const counts = progress?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const done = (counts.done ?? 0) + (counts.skipped ?? 0);
  const pct = total ? Math.round((done / total) * 100) : 0;
  const awaiting = counts.awaiting_input ?? 0;
  const cov = progress?.coverage;
  const status = progress?.status ?? "running";
  const active = ACTIVE.has(status) && !progress?.coverage?.complete;
  const phase =
    status === "done"
      ? "Готово"
      : status === "paused"
        ? "Выберите колонки"
        : awaiting > 0
          ? "Ожидают ваших ответов"
          : (counts.running ?? 0) > 0 || (counts.pending ?? 0) > 0
            ? "В работе"
            : "Готовлюсь";

  return (
    <div className="animate-fade-in space-y-6">
      <Link
        href={`/admin/sources/${params.id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" /> К источнику
      </Link>

      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Профилирование</h1>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            {status === "done" ? (
              <CheckCircle2 className="h-4 w-4 text-success" />
            ) : (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            )}
            {phase}
          </span>
          {active && progress?.agent_run_id && (
            <Tooltip label="Остановить профилирование">
              <Button variant="outline" size="sm" onClick={onCancel} className="gap-1">
                <X className="h-4 w-4" /> Прервать
              </Button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <Card>
        <CardContent className="space-y-3 py-4">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Задачи</span>
            <span className="font-medium">
              {done} / {total} ({pct}%)
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-brand-gradient transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            {(["running", "pending", "awaiting_input", "failed"] as const).map((k) =>
              counts[k] ? (
                <span key={k}>
                  {k}: <b className="text-foreground">{counts[k]}</b>
                </span>
              ) : null,
            )}
            {cov && cov.expected > 0 && (
              <span className="ml-auto">
                покрытие колонок:{" "}
                <b className={cov.complete ? "text-success" : "text-foreground"}>
                  {cov.covered}/{cov.expected}
                </b>
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {status === "paused" && (
        <ColumnSelectionGate runId={runId} onResume={refresh} />
      )}

      {progress && progress.questions.length > 0 && (
        <QuestionInbox groups={progress.questions} onAnswer={onAnswer} />
      )}

      {progress && progress.tasks.length > 0 && (
        <TaskBoard tasks={progress.tasks} />
      )}

      {status === "done" && (
        <Card className="border-success/40 bg-success/5">
          <CardContent className="flex items-center gap-2 py-4 text-sm">
            <CheckCircle2 className="h-5 w-5 text-success" />
            Профилирование завершено. Семантический слой и связи обновлены.
            <Link
              href={`/admin/sources/${params.id}`}
              className="ml-auto text-primary underline"
            >
              К таблицам источника
            </Link>
          </CardContent>
        </Card>
      )}

      {cov && !cov.complete && cov.missing.length > 0 && status === "done" && (
        <Card className="border-warning/40 bg-warning/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              Не описаны колонки ({cov.missing.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="font-mono text-xs text-muted-foreground">
            {cov.missing.slice(0, 30).map((m) => (
              <div key={`${m.database}.${m.table}.${m.column}`}>
                {m.database}.{m.table}.{m.column}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
