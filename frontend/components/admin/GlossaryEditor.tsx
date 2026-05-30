"use client";

import { BookText, Loader2, Save, Wand2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, HttpError } from "@/lib/api";
import type { DataSource } from "@/lib/types";

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ru-RU");
}

/**
 * Per-source glossary: house rules, field semantics, gold SQL and metrics.
 * Saved verbatim (injected into the agent's system prompt) and optionally
 * "разобрано" — structurally ingested into the semantic layer for retrieval.
 */
export function GlossaryEditor({
  source,
  onUpdated,
}: {
  source: DataSource;
  onUpdated: (s: DataSource) => void;
}) {
  const [draft, setDraft] = useState(source.glossary_md ?? "");
  const [saved, setSaved] = useState(source.glossary_md ?? "");
  const [saving, setSaving] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestedAt, setIngestedAt] = useState(source.glossary_ingested_at);

  const dirty = draft !== saved;
  const empty = draft.trim().length === 0;
  // Editing the glossary clears ingested_at on the server, so "stale" means we
  // have content saved but it hasn't been re-ingested yet.
  const needsIngest = !empty && !dirty && !ingestedAt;
  // "Разобрать" attaches field semantics + join relations to PROFILED tables;
  // before profiling they don't exist, so the ingest would silently drop them
  // and still mark itself done. Gate it until the source has been profiled
  // (the verbatim text still helps the describers, so Save stays open).
  const canIngest =
    source.profiling_status !== "never_profiled" &&
    source.profiling_status !== "in_progress";

  async function save() {
    setSaving(true);
    try {
      const updated = await api.sources.update(source.id, { glossary_md: draft });
      setSaved(draft);
      setIngestedAt(updated.glossary_ingested_at);
      onUpdated(updated);
      toast.success("Глоссарий сохранён");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  }

  async function ingest() {
    setIngesting(true);
    try {
      const res = await api.sources.ingestGlossary(source.id);
      if (!res.ok) {
        toast.error(res.warnings[0] || "Не удалось разобрать глоссарий");
        return;
      }
      setIngestedAt(new Date().toISOString());
      toast.success(
        `Разобрано: заметок ${res.notes} · метрик ${res.metrics} · терминов ${res.terms} · колонок ${res.columns} · связей ${res.relations}`,
      );
      for (const w of res.warnings) toast.warning(w);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка разбора");
    } finally {
      setIngesting(false);
    }
  }

  return (
    <Card className="brackets">
      <CardHeader>
        <span className="label-mono flex items-center gap-1.5">
          <BookText className="h-3.5 w-3.5" /> глоссарий источника
        </span>
        <CardTitle className="pt-1 text-base">House rules, поля, эталонные SQL и метрики</CardTitle>
        <CardDescription>
          Сохранённый текст агент всегда видит в системном промпте как авторитетный.
          «Разобрать» (доступно после профилирования) дополнительно раскладывает глоссарий
          в семантический слой — термины, метрики, заметки для поиска, смыслы полей и связи
          между таблицами, — чтобы агент находил это точечно через retrieval.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          rows={14}
          placeholder="# Таблица … — описание, поля, ## Правила, ## Метрики, эталонные SQL…"
          className="scroll-thin block w-full resize-y rounded-md border bg-muted/20 px-3 py-2.5 font-mono text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={saving || !dirty} size="sm" className="gap-1.5 font-mono">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Сохранить
          </Button>
          <Button
            onClick={ingest}
            disabled={ingesting || empty || dirty || !canIngest}
            variant="outline"
            size="sm"
            className="gap-1.5 font-mono"
            title="Разложить сохранённый глоссарий в семантический слой: термины, метрики, заметки (RAG), смыслы полей и связи между таблицами. Доступно после профилирования источника."
          >
            {ingesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            Разобрать
          </Button>
          <span className="label-mono ml-auto flex items-center gap-2">
            {dirty
              ? "есть несохранённые правки"
              : !canIngest
                ? "ожидает профилирования"
                : needsIngest
                  ? "сохранено · не разобрано"
                  : `разобрано: ${fmt(ingestedAt)}`}
            <span
              className={
                "h-1.5 w-1.5 rounded-full " +
                (dirty
                  ? "bg-warning"
                  : !canIngest
                    ? "bg-muted-foreground/40"
                    : needsIngest
                      ? "bg-warning/70"
                      : ingestedAt
                        ? "bg-success/70"
                        : "bg-muted-foreground/40")
              }
            />
          </span>
        </div>
        {!canIngest && !empty ? (
          <p className="font-mono text-[11px] text-muted-foreground">
            «Разобрать» станет доступно после профилирования источника: структурный разбор
            привязывает поля и связи к таблицам, которых сейчас ещё нет. Текст уже сохранён и
            помогает профайлеру.
          </p>
        ) : dirty ? (
          <p className="font-mono text-[11px] text-muted-foreground">
            Сохраните, чтобы «Разобрать» стало доступно — ингест работает по сохранённому тексту.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
