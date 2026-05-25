"use client";

import { ChevronLeft, RotateCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ColumnEditor } from "@/components/admin/ColumnEditor";
import { TableChat } from "@/components/admin/TableChat";
import { AgentStatusTimeline } from "@/components/chat/AgentStatusTimeline";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip } from "@/components/ui/tooltip";
import { useTask } from "@/hooks/useTask";
import { api, HttpError, type SemColumn, type SemTable } from "@/lib/api";
import { API_URL } from "@/lib/env";

type Tab = "overview" | "chat" | "history";

type Revision = {
  id: string;
  revision: number;
  payload: Record<string, unknown>;
  actor: string | null;
  reason: string | null;
  created_at: string;
};

export default function TableEditorPage() {
  const params = useParams<{ id: string; tableId: string }>();
  const [table, setTable] = useState<SemTable | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [domain, setDomain] = useState("");
  const [tags, setTags] = useState("");
  const [userNotes, setUserNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const task = useTask();

  function applyTable(t: SemTable) {
    setTable(t);
    setTitle(t.title || "");
    setDescription(t.description || "");
    setDomain(t.domain || "");
    setTags((t.tags || []).join(", "));
    setUserNotes(t.user_notes || "");
  }

  function load() {
    return api.tables
      .get(params.tableId)
      .then(applyTable)
      .catch((err) =>
        toast.error(err instanceof HttpError ? err.payload.message : "Ошибка"),
      );
  }

  function loadRevisions() {
    return api
      .tableRevisions(params.tableId)
      .then((r) => setRevisions(r as Revision[]))
      .catch(() => undefined);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.tableId]);

  useEffect(() => {
    if (tab === "history") loadRevisions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, params.tableId]);

  useEffect(() => {
    if (task.state === "done") load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.state]);

  async function onSave() {
    setSaving(true);
    try {
      const updated = await api.tables.update(params.tableId, {
        title,
        description,
        domain,
        tags: tags.split(",").map((s) => s.trim()).filter(Boolean),
        user_notes: userNotes,
        reason: "manual edit",
      });
      applyTable(updated);
      toast.success("Сохранено");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  }

  async function onConfirm() {
    try {
      const updated = await api.tables.confirm(params.tableId);
      applyTable(updated);
      toast.success("Подтверждено");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  async function onRegenerate() {
    try {
      const { agent_run_id } = await api.tables.regenerate(
        params.tableId,
        userNotes || null,
      );
      task.start(`${API_URL}/api/admin/edit/agent-runs/${agent_run_id}/events`);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  function onColumnUpdate(updated: SemColumn) {
    setTable((prev) =>
      prev
        ? {
            ...prev,
            columns: prev.columns.map((c) => (c.id === updated.id ? updated : c)),
          }
        : prev,
    );
  }

  if (!table) return <p className="text-muted-foreground">Загрузка…</p>;

  const regenerating = task.state === "running" || task.state === "connecting";

  return (
    <div className="animate-fade-in space-y-6">
      <Link
        href={`/admin/sources/${params.id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" /> К источнику
      </Link>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {table.title || `${table.database}.${table.table_name}`}
          </h1>
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{table.database}.{table.table_name}</span> · статус:{" "}
            <span className={table.confirmation_status === "confirmed" ? "text-success" : ""}>
              {table.confirmation_status}
            </span>
          </p>
        </div>
        <div className="flex gap-2">
          <Tooltip label="Заново описать таблицу через LLM, учитывая ваши комментарии">
            <Button
              onClick={onRegenerate}
              variant="outline"
              disabled={regenerating}
              className="gap-1.5"
            >
              <RotateCw className={"h-4 w-4" + (regenerating ? " animate-spin" : "")} />
              Перегенерировать
            </Button>
          </Tooltip>
          <Tooltip label="Пометить как проверенное — агент приоритетно опирается на подтверждённые таблицы">
            <Button onClick={onConfirm} variant="outline">
              Подтвердить
            </Button>
          </Tooltip>
          <Button onClick={onSave} disabled={saving}>
            {saving ? "Сохранение…" : "Сохранить"}
          </Button>
        </div>
      </div>

      <div className="flex gap-1 border-b">
        <TabButton active={tab === "overview"} onClick={() => setTab("overview")}>
          Описание и колонки
        </TabButton>
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Диалог с агентом
        </TabButton>
        <TabButton active={tab === "history"} onClick={() => setTab("history")}>
          История ревизий
        </TabButton>
      </div>

      {task.steps.length > 0 && task.state === "running" && (
        <AgentStatusTimeline steps={task.steps} />
      )}

      {tab === "overview" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Описание</CardTitle>
              <CardDescription>
                Эти поля попадают в семантический слой и md-заметку.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Заголовок</Label>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label>Описание</Label>
                <textarea
                  className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Домен</Label>
                  <Input value={domain} onChange={(e) => setDomain(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Теги (через запятую)</Label>
                  <Input value={tags} onChange={(e) => setTags(e.target.value)} />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Ваши комментарии</Label>
                <textarea
                  className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Учтётся при перегенерации описания"
                  value={userNotes}
                  onChange={(e) => setUserNotes(e.target.value)}
                />
              </div>
            </CardContent>
          </Card>

          <section>
            <h2 className="mb-3 text-lg font-medium">
              Колонки ({table.columns.length})
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Клик на описание — редактировать. Селект справа — поменять роль.
              Кнопки справа: перегенерировать описание колонки и подтвердить.
            </p>
            <div className="space-y-2">
              {table.columns.map((c) => (
                <ColumnEditor key={c.id} column={c} onUpdate={onColumnUpdate} />
              ))}
            </div>
          </section>
        </div>
      )}

      {tab === "chat" && (
        <TableChat tableId={params.tableId} onAfterApply={load} />
      )}

      {tab === "history" && (
        <RevisionsList revisions={revisions} />
      )}
    </div>
  );
}

function TabButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "border-b-2 px-3 py-2 text-sm transition " +
        (active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground")
      }
    >
      {children}
    </button>
  );
}

function RevisionsList({ revisions }: { revisions: Revision[] }) {
  if (revisions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        История пуста — ни одного изменения после первичного описания.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {revisions.map((r) => (
        <Card key={r.id}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              Ревизия #{r.revision}{" "}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {new Date(r.created_at).toLocaleString("ru-RU")}
                {r.actor && ` · ${r.actor}`}
                {r.reason && ` · ${r.reason}`}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">
              {JSON.stringify(r.payload, null, 2)}
            </pre>
            <p className="mt-1 text-xs text-muted-foreground">
              Снимок состояния ДО изменения. Чтобы откатить — скопируйте нужные
              поля в форму описания и сохраните.
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
