"use client";

import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, HttpError, type ColumnSelection } from "@/lib/api";

/**
 * The dry-run gate: after the structural harvest the run pauses here so the
 * admin can drop columns from the deep investigation. Excluded columns are
 * hidden from the agent (schema, RAG, graph, SQL) and never described by the
 * LLM. Submitting with nothing checked proceeds with every column kept.
 */
export function ColumnSelectionGate({
  runId,
  onResume,
}: {
  runId: string;
  onResume: () => void;
}) {
  const [data, setData] = useState<ColumnSelection | null>(null);
  // qname → set of disabled column names
  const [disabled, setDisabled] = useState<Record<string, Set<string>>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.profiling
      .columnSelection(runId)
      .then((d) => {
        setData(d);
        // first table expanded by default
        const first = d.tables[0];
        setOpen(first ? { [first.qname]: true } : {});
      })
      .catch((err) =>
        toast.error(err instanceof HttpError ? err.payload.message : "Ошибка"),
      );
  }, [runId]);

  const totalDisabled = useMemo(
    () => Object.values(disabled).reduce((a, s) => a + s.size, 0),
    [disabled],
  );

  function toggle(qname: string, name: string) {
    setDisabled((prev) => {
      const next = { ...prev };
      const set = new Set(next[qname] ?? []);
      if (set.has(name)) set.delete(name);
      else set.add(name);
      next[qname] = set;
      return next;
    });
  }

  async function submit() {
    if (!data) return;
    setSubmitting(true);
    try {
      const payload = data.tables
        .map((t) => ({
          table_id: t.table_id,
          names: Array.from(disabled[t.qname] ?? []),
        }))
        .filter((d) => d.names.length > 0);
      const res = await api.profiling.applyColumnSelection(runId, payload);
      toast.success(
        res.disabled
          ? `Исключено колонок: ${res.disabled}. Продолжаю исследование.`
          : "Все колонки сохранены. Продолжаю исследование.",
      );
      onResume();
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
      setSubmitting(false);
    }
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Загружаю собранную структуру…
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-primary/40">
      <CardHeader>
        <CardTitle className="text-base">Выберите колонки для исследования</CardTitle>
        <CardDescription>
          Структура собрана. Отметьте колонки,{" "}
          <strong className="font-semibold text-foreground">которые НЕ нужны</strong> —
          их пропустит LLM-описание, и агент их не увидит (схема, RAG, граф, SQL).
          Факты сохранятся, колонку можно вернуть позже.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.tables.map((t) => {
          const off = disabled[t.qname]?.size ?? 0;
          const isOpen = open[t.qname] ?? false;
          return (
            <div key={t.table_id} className="rounded-md border">
              <button
                type="button"
                onClick={() => setOpen((p) => ({ ...p, [t.qname]: !isOpen }))}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50"
              >
                {isOpen ? (
                  <ChevronDown className="h-4 w-4 shrink-0" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0" />
                )}
                <span className="font-mono">{t.qname}</span>
                <span className="text-xs text-muted-foreground">
                  {t.columns.length} колонок
                  {off > 0 && ` · исключено ${off}`}
                </span>
              </button>
              {isOpen && (
                <div className="border-t px-3 py-2">
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {t.columns.map((c) => {
                      const dropped = disabled[t.qname]?.has(c.name) ?? false;
                      return (
                        <label
                          key={c.name}
                          className={
                            "flex cursor-pointer items-start gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-muted/50" +
                            (dropped ? " opacity-50" : "")
                          }
                        >
                          <input
                            type="checkbox"
                            checked={dropped}
                            onChange={() => toggle(t.qname, c.name)}
                            className="mt-0.5"
                          />
                          <span className="min-w-0">
                            <span
                              className={
                                "font-mono" + (dropped ? " line-through" : "")
                              }
                            >
                              {c.name}
                            </span>{" "}
                            <span className="text-muted-foreground">
                              {c.data_type}
                              {c.semantic_role ? ` · ${c.semantic_role}` : ""}
                              {c.distinct_count != null
                                ? ` · distinct ${c.distinct_count}`
                                : ""}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        <div className="flex items-center justify-between pt-1">
          <span className="text-xs text-muted-foreground">
            {totalDisabled > 0
              ? `К исключению: ${totalDisabled}`
              : "Ничего не исключено — продолжим со всеми"}
          </span>
          <Button onClick={submit} disabled={submitting} className="gap-1.5">
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {totalDisabled > 0
              ? `Исключить ${totalDisabled} и продолжить`
              : "Продолжить со всеми"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
