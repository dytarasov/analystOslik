"use client";

import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  HelpCircle,
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ProfilingQuestionGroup } from "@/lib/api";

type AnswerPayload = { column?: string | null; text?: string | null; answer: string };

const PAGE_SIZE = 5;

export function QuestionInbox({
  groups,
  onAnswer,
}: {
  groups: ProfilingQuestionGroup[];
  onAnswer: (taskId: string, answers: AnswerPayload[]) => Promise<void>;
}) {
  // Answer drafts live here (not in the cards) so they survive both the 1.5s
  // poll that swaps `groups` and pagination that unmounts off-screen cards.
  const [drafts, setDrafts] = useState<Record<string, Record<number, string>>>({});
  const [open, setOpen] = useState<Record<string, boolean | undefined>>({});
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});
  const [page, setPage] = useState(0);

  // Stable insertion order: a group keeps its slot across polls and newly
  // arrived groups are appended at the bottom, so the list never reshuffles
  // under the user's cursor.
  const orderRef = useRef<Map<string, number>>(new Map());
  const sorted = useMemo(() => {
    let next = orderRef.current.size;
    for (const g of groups) {
      if (!orderRef.current.has(g.task_id)) orderRef.current.set(g.task_id, next++);
    }
    return [...groups].sort(
      (a, b) => orderRef.current.get(a.task_id)! - orderRef.current.get(b.task_id)!,
    );
  }, [groups]);

  if (sorted.length === 0) return null;

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageGroups = sorted.slice(
    clampedPage * PAGE_SIZE,
    clampedPage * PAGE_SIZE + PAGE_SIZE,
  );

  // First group is expanded by default; everything else (incl. groups that
  // arrive later) stays collapsed until the user opens it.
  const firstId = sorted[0]?.task_id;
  const isOpen = (id: string) => open[id] ?? id === firstId;

  function setAnswer(taskId: string, i: number, v: string) {
    setDrafts((prev) => ({ ...prev, [taskId]: { ...(prev[taskId] ?? {}), [i]: v } }));
  }

  function answeredCount(g: ProfilingQuestionGroup): number {
    const d = drafts[g.task_id] ?? {};
    return g.questions.filter((_, i) => (d[i] ?? "").trim()).length;
  }

  async function submit(g: ProfilingQuestionGroup) {
    const d = drafts[g.task_id] ?? {};
    const payload: AnswerPayload[] = g.questions
      .map((q, i) => ({
        column: q.column ?? null,
        text: q.text,
        answer: (d[i] ?? "").trim(),
      }))
      .filter((a) => a.answer);
    if (!payload.length) return;
    setSubmitting((s) => ({ ...s, [g.task_id]: true }));
    try {
      await onAnswer(g.task_id, payload);
      setDrafts((prev) => {
        const n = { ...prev };
        delete n[g.task_id];
        return n;
      });
    } finally {
      setSubmitting((s) => ({ ...s, [g.task_id]: false }));
    }
  }

  const totalAnswered = sorted.reduce((acc, g) => acc + (answeredCount(g) > 0 ? 1 : 0), 0);

  function setAll(value: boolean) {
    setOpen(Object.fromEntries(sorted.map((g) => [g.task_id, value])));
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
        <HelpCircle className="h-4 w-4 text-primary" />
        Вопросы по таблицам
        <Badge variant="warning">{sorted.length}</Badge>
        {totalAnswered > 0 && (
          <span className="text-xs font-normal text-muted-foreground">
            начато: {totalAnswered}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setAll(true)}>
            Развернуть все
          </Button>
          <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setAll(false)}>
            Свернуть все
          </Button>
        </div>
      </div>

      {pageGroups.map((g) => {
        const answered = answeredCount(g);
        const opened = isOpen(g.task_id);
        return (
          <Card key={g.task_id} className="overflow-hidden border-brand-200 bg-brand-50/30">
            <button
              type="button"
              onClick={() => setOpen((o) => ({ ...o, [g.task_id]: !opened }))}
              className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-brand-50/60"
            >
              <ChevronDown
                className={cn(
                  "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                  !opened && "-rotate-90",
                )}
              />
              <span className="min-w-0 flex-1 truncate font-mono text-sm text-muted-foreground">
                {g.database}.{g.table}
              </span>
              <Badge variant={answered === g.questions.length ? "success" : "secondary"}>
                {answered}/{g.questions.length}
              </Badge>
            </button>

            {opened && (
              <CardContent className="space-y-3 border-t pt-3">
                {g.questions.map((q, i) => {
                  const choices = q.choices ?? [];
                  const value = drafts[g.task_id]?.[i] ?? "";
                  const custom = value && !choices.includes(value) ? value : "";
                  return (
                    <div key={i} className="space-y-2 rounded-lg border bg-card p-3">
                      <div className="text-sm font-medium">
                        {q.column && (
                          <span className="mr-1.5 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                            {q.column}
                          </span>
                        )}
                        {q.text}
                      </div>
                      {choices.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {choices.map((c) => {
                            const selected = value === c;
                            return (
                              <Button
                                key={c}
                                size="sm"
                                variant={selected ? "default" : "outline"}
                                // Toggle: clicking the selected choice clears it, so
                                // the user can deselect and answer with free text.
                                onClick={() => setAnswer(g.task_id, i, selected ? "" : c)}
                                title={selected ? "Нажмите ещё раз, чтобы снять выбор" : undefined}
                              >
                                {c}
                              </Button>
                            );
                          })}
                        </div>
                      )}
                      <Input
                        placeholder={choices.length ? "или впишите свой ответ…" : "Ваш ответ…"}
                        value={custom}
                        onChange={(e) => setAnswer(g.task_id, i, e.target.value)}
                        onKeyDown={(e) => e.stopPropagation()}
                      />
                    </div>
                  );
                })}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    Отвечено: {answered} из {g.questions.length}
                  </span>
                  <Button
                    onClick={() => submit(g)}
                    disabled={submitting[g.task_id] || answered === 0}
                    className="gap-1.5"
                  >
                    <Check className="h-4 w-4" />
                    {submitting[g.task_id] ? "Отправляю…" : "Ответить и продолжить"}
                  </Button>
                </div>
              </CardContent>
            )}
          </Card>
        );
      })}

      {pageCount > 1 && (
        <div className="flex items-center justify-center gap-3 pt-1">
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            disabled={clampedPage === 0}
            onClick={() => setPage(clampedPage - 1)}
          >
            <ChevronLeft className="h-4 w-4" /> Назад
          </Button>
          <span className="text-xs text-muted-foreground">
            стр. {clampedPage + 1} из {pageCount}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="gap-1"
            disabled={clampedPage >= pageCount - 1}
            onClick={() => setPage(clampedPage + 1)}
          >
            Вперёд <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  );
}
