"use client";

import {
  Check,
  Circle,
  CircleAlert,
  Clock,
  HelpCircle,
  Loader2,
  MinusCircle,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { ProfilingTask } from "@/lib/api";

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />;
    case "done":
      return <Check className="h-3.5 w-3.5 text-success" />;
    case "failed":
      return <CircleAlert className="h-3.5 w-3.5 text-destructive" />;
    case "awaiting_input":
      return <HelpCircle className="h-3.5 w-3.5 text-warning" />;
    case "skipped":
      return <MinusCircle className="h-3.5 w-3.5 text-muted-foreground" />;
    case "blocked":
      return <Clock className="h-3.5 w-3.5 text-muted-foreground" />;
    default:
      return <Circle className="h-3.5 w-3.5 text-muted-foreground/50" />;
  }
}

function taskLabel(t: ProfilingTask): string {
  if (t.kind === "harvest_table") return "Сбор структуры";
  if (t.kind === "relations") return "Связи между таблицами";
  if (t.kind === "synthesize") return "Синтез источника";
  if (t.kind === "describe_group") {
    if (t.target.endsWith("#__table__")) return "Описание таблицы";
    return t.columns.length ? t.columns.join(", ") : "Колонки";
  }
  return t.kind;
}

function fmtDuration(t: ProfilingTask, now: number): string {
  const start = t.started_at ? Date.parse(t.started_at) : null;
  if (!start) return "";
  const end = t.finished_at ? Date.parse(t.finished_at) : now;
  const sec = Math.max(0, (end - start) / 1000);
  return `${sec.toFixed(sec < 10 ? 1 : 0)}с`;
}

export function TaskBoard({ tasks }: { tasks: ProfilingTask[] }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (tasks.length === 0) return null;

  // Group by table; table-less tasks (relations) go to a run-level bucket.
  const byTable = new Map<string, ProfilingTask[]>();
  const runLevel: ProfilingTask[] = [];
  for (const t of tasks) {
    if (t.table_name) {
      const key = `${t.database}.${t.table_name}`;
      if (!byTable.has(key)) byTable.set(key, []);
      byTable.get(key)!.push(t);
    } else {
      runLevel.push(t);
    }
  }

  return (
    <div className="space-y-3">
      <div className="text-sm font-medium">Задачи профилирования</div>
      {[...byTable.entries()].map(([table, group]) => (
        <TaskGroup key={table} title={table} mono tasks={group} now={now} />
      ))}
      {runLevel.length > 0 && (
        <TaskGroup title="Источник" tasks={runLevel} now={now} />
      )}
    </div>
  );
}

function TaskGroup({
  title,
  tasks,
  now,
  mono = false,
}: {
  title: string;
  tasks: ProfilingTask[];
  now: number;
  mono?: boolean;
}) {
  const done = tasks.filter((t) => t.status === "done" || t.status === "skipped").length;
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between border-b bg-muted/30 px-3 py-1.5">
        <span className={cn("text-sm font-medium", mono && "font-mono")}>{title}</span>
        <span className="text-xs text-muted-foreground">
          {done}/{tasks.length}
        </span>
      </div>
      <CardContent className="space-y-0.5 p-2">
        {tasks.map((t) => (
          <div
            key={t.id}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1 text-sm transition-colors",
              t.status === "running" && "bg-primary/5",
              t.status === "awaiting_input" && "bg-warning/10",
              t.status === "failed" && "bg-destructive/5",
            )}
          >
            <StatusIcon status={t.status} />
            <span
              className={cn(
                "min-w-0 flex-1 truncate",
                t.status === "pending" && "text-muted-foreground",
              )}
              title={taskLabel(t)}
            >
              {taskLabel(t)}
            </span>
            {t.attempts > 1 && (
              <span
                className="rounded bg-warning/15 px-1.5 text-[10px] text-warning"
                title={`Попыток: ${t.attempts}`}
              >
                ×{t.attempts}
              </span>
            )}
            {t.error && (
              <span className="max-w-[160px] truncate text-[11px] text-destructive" title={t.error}>
                {t.error}
              </span>
            )}
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              {fmtDuration(t, now)}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
