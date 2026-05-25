"use client";

import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Loader2,
  Play,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError } from "@/lib/api";
import { cn } from "@/lib/utils";

type RerunResult = {
  sql: string;
  preview: { columns: string[]; rows: unknown[][] };
  rowcount: number;
  exportUrl: string | null;
};

export function EditableSqlBlock({
  sql,
  taskId,
  onRerunSuccess,
}: {
  sql: string;
  taskId: string;
  onRerunSuccess?: (r: RerunResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(sql);
  const [copied, setCopied] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errKind, setErrKind] = useState<string | null>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Keep the editor in sync if a new SQL comes in (e.g. after rerun in another
  // block referring to the same task).
  useEffect(() => {
    setDraft(sql);
  }, [sql]);

  // Auto-grow textarea to fit content (Jupyter-style).
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 360)}px`;
  }, [draft, open]);

  async function onCopy() {
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* noop */
    }
  }

  async function run() {
    if (!draft.trim() || running) return;
    setRunning(true);
    setError(null);
    setErrKind(null);
    try {
      const res = await api.client.rerunSql(taskId, draft.trim());
      if (res.ok) {
        onRerunSuccess?.({
          sql: res.sql,
          preview: res.preview,
          rowcount: res.rowcount,
          exportUrl: res.export_url,
        });
        setDraft(res.sql); // normalised by sql_guard (LIMIT/SETTINGS injected)
      } else {
        setError(res.error);
        setErrKind(res.kind);
      }
    } catch (err) {
      setError(err instanceof HttpError ? err.payload.message : "Ошибка сети");
    } finally {
      setRunning(false);
    }
  }

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      run();
    }
  }

  const isDirty = draft.trim() !== sql.trim();

  return (
    <div
      className={cn(
        "rounded-lg border bg-card",
        error && "border-destructive/40",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-3 py-2 text-sm text-muted-foreground hover:bg-muted/30"
      >
        <span className="flex items-center gap-2">
          {open ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
          SQL запрос
          {isDirty && open && (
            <span className="ml-1 rounded-sm bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning">
              не сохранено
            </span>
          )}
        </span>
        {open && (
          <span className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <Tooltip label={copied ? "Скопировано" : "Скопировать SQL"}>
              <Button type="button" size="sm" variant="ghost" onClick={onCopy}>
                {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
              </Button>
            </Tooltip>
            <Tooltip label="Выполнить запрос (⌘↵)">
              <Button
                type="button"
                size="sm"
                onClick={run}
                disabled={running || !draft.trim()}
                className="transition-transform active:scale-95"
              >
                {running ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                <span className="ml-1.5 text-xs">Запустить</span>
              </Button>
            </Tooltip>
          </span>
        )}
      </button>

      {open && (
        <div className="animate-fade-in-down border-t">
          <textarea
            ref={taRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            spellCheck={false}
            className="block w-full resize-none bg-muted/20 px-3 py-3 font-mono text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            style={{ minHeight: 80 }}
          />
          <div className="border-t bg-muted/10 px-3 py-1.5 text-[10px] text-muted-foreground">
            ⌘+Enter (Ctrl+Enter) — выполнить · правки сохраняются после успешного запуска
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 border-t bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="font-medium">
              {errKind === "guard"
                ? "SQL не прошёл проверку безопасности"
                : errKind === "parse"
                  ? "Не удалось разобрать SQL"
                  : errKind === "execute"
                    ? "Ошибка выполнения в ClickHouse"
                    : "Ошибка"}
            </div>
            <div className="whitespace-pre-wrap break-words">{error}</div>
          </div>
        </div>
      )}
    </div>
  );
}
