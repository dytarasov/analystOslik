"use client";

import { AlertCircle, Check, ChevronRight, Copy, Loader2, Play } from "lucide-react";
import Prism from "prismjs";
import "prismjs/components/prism-sql";
import { useEffect, useMemo, useRef, useState } from "react";
import Editor from "react-simple-code-editor";
import { format } from "sql-formatter";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError } from "@/lib/api";
import { cn } from "@/lib/utils";

export type RerunResult = {
  cellId: string;
  sql: string;
  preview: { columns: string[]; rows: unknown[][] };
  rowcount: number;
  exportUrl: string | null;
};

// Pretty-print the query so a long one-liner becomes a readable, indented block
// (SELECT columns each on their own line, clauses broken out). Best-effort: if the
// formatter trips on ClickHouse-specific syntax, we keep the original text.
function prettySql(raw: string): string {
  try {
    return format(raw, { language: "sql", keywordCase: "upper", tabWidth: 2 });
  } catch {
    return raw.trim();
  }
}

export function EditableSqlBlock({
  sql,
  taskId,
  sourceName,
  onRerunSuccess,
}: {
  sql: string;
  taskId: string;
  sourceName?: string | null;
  onRerunSuccess?: (r: RerunResult) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(() => prettySql(sql));
  const [copied, setCopied] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errKind, setErrKind] = useState<string | null>(null);
  const editorWrapRef = useRef<HTMLDivElement>(null);
  // prism-sql is imported above, so the grammar is registered at runtime.
  const highlight = (code: string) => Prism.highlight(code, Prism.languages.sql!, "sql");

  // Baseline = the formatted incoming SQL; dirty is measured against it (so just
  // pretty-printing doesn't read as an unsaved edit).
  const pretty = useMemo(() => prettySql(sql), [sql]);

  // Keep the editor in sync if a new SQL comes in (e.g. after a rerun elsewhere
  // referring to the same task). The editor auto-grows to its content.
  useEffect(() => {
    setDraft(pretty);
  }, [pretty]);

  const isDirty = draft.trim() !== pretty.trim();
  const lineCount = useMemo(() => draft.split("\n").length, [draft]);
  const firstLine = useMemo(
    () => (pretty.split("\n").find((l) => l.trim()) || pretty).trim(),
    [pretty],
  );

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
        // Jupyter-style: the run's output becomes a NEW cell below — we leave the
        // editor's text exactly as the user typed it (the executed/normalised SQL
        // is shown in the new cell's own block) so this source stays editable.
        onRerunSuccess?.({
          cellId: res.cell_id,
          sql: res.sql,
          preview: res.preview,
          rowcount: res.rowcount,
          exportUrl: res.export_url,
        });
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

  // ⌘/Ctrl+Enter to run. We attach a NATIVE keydown listener on the textarea
  // (not React's synthetic onKeyDown): it fires in the element's target phase, so
  // it works even if something up the tree swallows the synthetic event, and it
  // sidesteps any synthetic-event delegation quirks. runRef keeps it calling the
  // latest run() without re-binding on every keystroke.
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    if (!open) return;
    const el = editorWrapRef.current?.querySelector("textarea");
    if (!el) return;
    const handler = (e: KeyboardEvent) => {
      const isEnter =
        e.key === "Enter" || e.code === "Enter" || e.code === "NumpadEnter";
      if ((e.metaKey || e.ctrlKey) && isEnter) {
        e.preventDefault();
        void runRef.current();
      }
    };
    el.addEventListener("keydown", handler);
    return () => el.removeEventListener("keydown", handler);
  }, [open]);

  return (
    <div
      className={cn(
        "brackets overflow-hidden rounded-md border bg-card transition-colors",
        error && "border-destructive/40",
      )}
    >
      {/* Toggle row — stable layout: never changes content/width on open/close,
          so expanding doesn't shift things around. */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/30"
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
            open && "rotate-90",
          )}
        />
        <span className="label-mono shrink-0">sql</span>
        {open ? (
          <span className="label-mono ml-1 shrink-0 text-muted-foreground/70">
            {lineCount} стр.
          </span>
        ) : (
          <code
            className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground"
            dangerouslySetInnerHTML={{ __html: highlight(firstLine) }}
          />
        )}
        {isDirty && (
          <span className="ml-auto shrink-0 rounded-sm bg-warning/15 px-1.5 py-0.5 font-mono text-[10px] text-warning">
            изменён
          </span>
        )}
      </button>

      {open && (
        <div className="animate-fade-in-down border-t">
          {/* Toolbar lives in the body, not the header, so toggling never
              reflows the toggle row. */}
          <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5">
            <span className="label-mono truncate text-muted-foreground/80">
              {sourceName || "источник"}
            </span>
            <div className="flex items-center gap-1">
              <Tooltip label={copied ? "Скопировано" : "Скопировать SQL"}>
                <Button type="button" size="sm" variant="ghost" onClick={onCopy} className="h-7 px-2">
                  {copied ? (
                    <Check className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </Button>
              </Tooltip>
              <Button
                type="button"
                size="sm"
                onClick={run}
                disabled={running || !draft.trim()}
                className="h-7 gap-1.5 px-2.5 font-mono transition-transform active:scale-95"
              >
                {running ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                <span className="text-xs">Запустить</span>
              </Button>
            </div>
          </div>

          {/* The code lives in its own framed, lighter panel (rounded border +
              card surface on a faint gutter) so it reads as a proper editor. */}
          <div className="bg-muted/20 p-2.5">
            <div
              ref={editorWrapRef}
              className="scroll-thin max-h-[460px] overflow-auto rounded-lg border bg-card shadow-sm"
            >
              <Editor
                value={draft}
                onValueChange={setDraft}
                highlight={highlight}
                padding={16}
                textareaClassName="caret-foreground focus:outline-none"
                style={{
                  fontFamily: "var(--font-mono), ui-monospace, monospace",
                  fontSize: 12.5,
                  lineHeight: 1.7,
                  minHeight: 96,
                }}
              />
            </div>
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
