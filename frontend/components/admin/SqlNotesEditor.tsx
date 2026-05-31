"use client";

import { Code, Loader2, Save, Wand2 } from "lucide-react";
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

const PLACEHOLDER = `## Активации учителей по месяцам
Когда: нужна динамика активаций по периодам.

\`\`\`sql
SELECT toStartOfMonth(event_time) AS month, uniqExact(user_id) AS teachers
FROM librarium.api_user_events
WHERE kind = 'activation'
GROUP BY month ORDER BY month
\`\`\`

## …следующий рецепт
`;

/**
 * Per-source SQL notes: typical/gold SQL recipes kept SEPARATE from the glossary
 * (which is prompt-injected). "Разобрать" splits them into recipes; the agent
 * retrieves the relevant one via find_sql_recipes before writing SQL. Only the
 * natural-language intent is embedded — the SQL itself is stored verbatim.
 */
export function SqlNotesEditor({
  source,
  onUpdated,
}: {
  source: DataSource;
  onUpdated: (s: DataSource) => void;
}) {
  const [draft, setDraft] = useState(source.sql_notes_md ?? "");
  const [saved, setSaved] = useState(source.sql_notes_md ?? "");
  const [saving, setSaving] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [ingestedAt, setIngestedAt] = useState(source.sql_notes_ingested_at);

  const dirty = draft !== saved;
  const empty = draft.trim().length === 0;
  const needsIngest = !empty && !dirty && !ingestedAt;

  async function save() {
    setSaving(true);
    try {
      const updated = await api.sources.update(source.id, { sql_notes_md: draft });
      setSaved(draft);
      setIngestedAt(updated.sql_notes_ingested_at);
      onUpdated(updated);
      toast.success("SQL-заметки сохранены");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  }

  async function ingest() {
    setIngesting(true);
    try {
      const res = await api.sources.ingestSqlNotes(source.id);
      if (!res.ok) {
        toast.error(res.warnings[0] || "Не удалось разобрать SQL-заметки");
        return;
      }
      setIngestedAt(new Date().toISOString());
      toast.success(`Разобрано рецептов: ${res.recipes}`);
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
          <Code className="h-3.5 w-3.5" /> SQL-заметки источника
        </span>
        <CardTitle className="pt-1 text-base">Типовые запросы и эталонные SQL</CardTitle>
        <CardDescription>
          Готовые SQL-рецепты этого источника. «Разобрать» режет их на отдельные
          рецепты (название · когда применять · дословный SQL). Агент ОБЯЗАН свериться
          с ними перед построением запроса (инструмент <span className="font-mono">find_sql_recipes</span>)
          и берёт подходящий за основу. В отличие от глоссария, в промпт целиком НЕ
          вставляются — находятся точечно по смыслу задачи.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          spellCheck={false}
          rows={14}
          placeholder={PLACEHOLDER}
          className="scroll-thin block w-full resize-y rounded-md border bg-muted/20 px-3 py-2.5 font-mono text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={save} disabled={saving || !dirty} size="sm" className="gap-1.5 font-mono">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            Сохранить
          </Button>
          <Button
            onClick={ingest}
            disabled={ingesting || empty || dirty}
            variant="outline"
            size="sm"
            className="gap-1.5 font-mono"
            title="Разобрать сохранённые SQL-заметки на рецепты, по которым агент сверяется перед написанием SQL."
          >
            {ingesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
            Разобрать
          </Button>
          <span className="label-mono ml-auto flex items-center gap-2">
            {dirty
              ? "есть несохранённые правки"
              : needsIngest
                ? "сохранено · не разобрано"
                : `разобрано: ${fmt(ingestedAt)}`}
            <span
              className={
                "h-1.5 w-1.5 rounded-full " +
                (dirty
                  ? "bg-warning"
                  : needsIngest
                    ? "bg-warning/70"
                    : ingestedAt
                      ? "bg-success/70"
                      : "bg-muted-foreground/40")
              }
            />
          </span>
        </div>
        {dirty ? (
          <p className="font-mono text-[11px] text-muted-foreground">
            Сохраните, чтобы «Разобрать» стало доступно — разбор работает по сохранённому тексту.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
