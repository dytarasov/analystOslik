"use client";

import { Eye, EyeOff, Loader2, RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { AgentStatusTimeline } from "@/components/chat/AgentStatusTimeline";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { useTask } from "@/hooks/useTask";
import { api, HttpError, type SemColumn } from "@/lib/api";
import { API_URL } from "@/lib/env";

const ROLES = [
  "id",
  "fk",
  "measure",
  "dimension",
  "timestamp",
  "flag",
  "free_text",
] as const;

const ROLE_LABEL: Record<string, string> = {
  id: "идентификатор",
  fk: "внешний ключ",
  measure: "метрика",
  dimension: "измерение",
  timestamp: "время",
  flag: "флаг",
  free_text: "текст",
};

export function ColumnEditor({
  column,
  onUpdate,
}: {
  column: SemColumn;
  onUpdate: (col: SemColumn) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(column.description || "");
  const [role, setRole] = useState(column.semantic_role || "");
  const [notes, setNotes] = useState(column.user_notes || "");
  const [notesOpen, setNotesOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [reprofiling, setReprofiling] = useState(false);
  const task = useTask();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setDraft(column.description || "");
    setRole(column.semantic_role || "");
    setNotes(column.user_notes || "");
  }, [column.id, column.description, column.semantic_role, column.user_notes]);

  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(
        textareaRef.current.value.length,
        textareaRef.current.value.length,
      );
    }
  }, [editing]);

  useEffect(() => {
    if (task.state === "done") {
      api.columns.get(column.id).then((c) => onUpdate(c as SemColumn));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.state]);

  async function saveDescription() {
    if (draft === (column.description || "")) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const updated = await api.columns.update(column.id, {
        description: draft,
        reason: "manual edit",
      });
      onUpdate(updated as SemColumn);
      toast.success("Описание сохранено");
      setEditing(false);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  }

  async function saveRole(newRole: string) {
    setRole(newRole);
    try {
      const updated = await api.columns.update(column.id, {
        semantic_role: newRole,
        reason: "manual role change",
      });
      onUpdate(updated as SemColumn);
    } catch (err) {
      setRole(column.semantic_role || "");
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  async function saveNotes() {
    if (notes === (column.user_notes || "")) {
      setNotesOpen(false);
      return;
    }
    try {
      const updated = await api.columns.update(column.id, {
        user_notes: notes,
        reason: "user notes",
      });
      onUpdate(updated as SemColumn);
      toast.success("Комментарий сохранён");
      setNotesOpen(false);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  async function onToggleEnabled() {
    if (toggling) return;
    const next = !(column.enabled ?? true);
    setToggling(true);
    try {
      await api.columns.update(column.id, {
        enabled: next,
        reason: next ? "включение колонки" : "отключение колонки",
      });
      const fresh = await api.columns.get(column.id);
      onUpdate(fresh as SemColumn);
      toast.success(
        next ? "Колонка включена в исследование" : "Колонка исключена из исследования",
      );
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setToggling(false);
    }
  }

  async function onReprofile() {
    if (reprofiling) return;
    setReprofiling(true);
    try {
      await api.columns.reprofile(column.id);
      const fresh = await api.columns.get(column.id);
      onUpdate(fresh as SemColumn);
      toast.success("Колонка доисследована");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setReprofiling(false);
    }
  }

  async function onRegenerate() {
    try {
      const { agent_run_id } = await api.columns.regenerate(
        column.id,
        notes || null,
      );
      task.start(`${API_URL}/api/admin/edit/agent-runs/${agent_run_id}/events`);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  const isRegen = task.state === "running" || task.state === "connecting";
  const isEnabled = column.enabled ?? true;

  return (
    <div
      className={
        "rounded-md border bg-card transition hover:border-primary/30" +
        (isEnabled ? "" : " border-dashed opacity-55")
      }
    >
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-sm">{column.name}</span>
          <span className="text-xs text-muted-foreground">: {column.data_type}</span>
          {!isEnabled && (
            <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              исключена
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Tooltip
            label={
              isEnabled
                ? "Исключить колонку из исследования — агент перестанет её видеть (схема, RAG, граф, SQL-guard)"
                : "Вернуть колонку в исследование"
            }
          >
            <button
              type="button"
              onClick={onToggleEnabled}
              disabled={toggling}
              className="rounded-md border px-2 py-1 text-xs transition-colors hover:bg-muted disabled:opacity-50"
            >
              {toggling ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : isEnabled ? (
                <Eye className="h-3.5 w-3.5" />
              ) : (
                <EyeOff className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </button>
          </Tooltip>
          {isEnabled && (
            <select
              value={role}
              onChange={(e) => saveRole(e.target.value)}
              className="rounded-md border bg-background px-2 py-1 text-xs"
            >
              <option value="">— роль —</option>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          )}
          {isEnabled && !column.description && (
            <Tooltip label="Доисследовать: LLM опишет колонку по собранным фактам и обновит заметки/граф">
              <button
                type="button"
                onClick={onReprofile}
                disabled={reprofiling}
                className="inline-flex items-center gap-1 rounded-md border border-primary/40 px-2 py-1 text-xs text-primary transition-colors hover:bg-primary/10 disabled:opacity-50"
              >
                {reprofiling && <Loader2 className="h-3 w-3 animate-spin" />}
                доисследовать
              </button>
            </Tooltip>
          )}
          {isEnabled && (
            <Tooltip label="Перегенерировать описание этой колонки (учтёт ваш комментарий)">
              <button
                type="button"
                onClick={onRegenerate}
                disabled={isRegen}
                className="rounded-md border px-2 py-1 text-xs transition-colors hover:bg-muted disabled:opacity-50"
              >
                <RotateCw className={"h-3.5 w-3.5" + (isRegen ? " animate-spin" : "")} />
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="px-3 py-2">
        {!isEnabled ? (
          // Excluded columns aren't editable: a saved description here would be
          // ignored by the agent/RAG/graph (enabled=false filters it out), which
          // made users think "I saved it but it vanished". Re-enable to edit.
          <div className="text-sm">
            <span className="text-muted-foreground italic">
              {column.description || "нет описания"}
            </span>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Колонка исключена из исследования — верните её иконкой глаза, чтобы
              описание учитывалось и его можно было редактировать.
            </p>
          </div>
        ) : editing ? (
          <div className="space-y-2">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") saveDescription();
                if (e.key === "Escape") {
                  setDraft(column.description || "");
                  setEditing(false);
                }
              }}
              className="flex min-h-[64px] w-full rounded-md border bg-background px-2 py-1 text-sm"
              placeholder="Описание колонки…"
            />
            <div className="flex justify-end gap-2 text-xs">
              <button
                type="button"
                onClick={() => {
                  setDraft(column.description || "");
                  setEditing(false);
                }}
                className="rounded-md border px-2 py-1 hover:bg-muted"
              >
                Отмена
              </button>
              <Button size="sm" disabled={saving} onClick={saveDescription}>
                {saving ? "…" : "Сохранить (⌘↵)"}
              </Button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="block w-full cursor-text text-left text-sm hover:text-foreground"
            title="Клик для редактирования"
          >
            {column.description || (
              <span className="text-muted-foreground italic">
                нет описания — кликните чтобы добавить
              </span>
            )}
          </button>
        )}

        <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-foreground">
          {column.null_ratio !== null && (
            <span>null: {(Number(column.null_ratio) * 100).toFixed(1)}%</span>
          )}
          {column.distinct_count !== null && <span>distinct: {column.distinct_count}</span>}
          <button
            type="button"
            onClick={() => setNotesOpen((v) => !v)}
            className="ml-auto hover:text-foreground"
          >
            {notesOpen ? "скрыть комментарий" : column.user_notes ? "комментарий ✎" : "+ комментарий"}
          </button>
        </div>

        {notesOpen && (
          <div className="mt-2 space-y-2">
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Учтётся при следующей перегенерации этой колонки…"
              className="flex min-h-[48px] w-full rounded-md border bg-background px-2 py-1 text-xs"
            />
            <div className="flex justify-end">
              <Button size="sm" variant="outline" onClick={saveNotes}>
                Сохранить
              </Button>
            </div>
          </div>
        )}

        {task.steps.length > 0 && isRegen && (
          <div className="mt-2">
            <AgentStatusTimeline steps={task.steps} />
          </div>
        )}
      </div>
    </div>
  );
}
