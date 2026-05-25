"use client";

import { Check, RotateCw } from "lucide-react";
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

  async function onConfirm() {
    try {
      const updated = await api.columns.confirm(column.id);
      onUpdate(updated as SemColumn);
      toast.success("Подтверждено");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
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

  const isConfirmed = column.confirmation_status === "confirmed";
  const isRegen = task.state === "running" || task.state === "connecting";

  return (
    <div className="rounded-md border bg-card transition hover:border-primary/30">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-sm">{column.name}</span>
          <span className="text-xs text-muted-foreground">: {column.data_type}</span>
          {isConfirmed && (
            <span className="rounded-sm bg-success/10 px-1.5 py-0.5 text-[10px] text-success">
              подтверждено
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
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
          {!isConfirmed && (
            <Tooltip label="Подтвердить — агент будет приоритетно опираться на эту колонку при сборке SQL">
              <button
                type="button"
                onClick={onConfirm}
                className="rounded-md border px-2 py-1 text-xs text-success transition-colors hover:bg-success/10"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="px-3 py-2">
        {editing ? (
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
