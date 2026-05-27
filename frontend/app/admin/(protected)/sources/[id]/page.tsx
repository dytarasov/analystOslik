"use client";

import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { EditSourceDialog } from "@/components/admin/EditSourceDialog";
import { GlossaryEditor } from "@/components/admin/GlossaryEditor";
import { ProfilingStatusBadge } from "@/components/admin/ProfilingStatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError, type ProfilingRun, type SemTableRow } from "@/lib/api";
import type { DataSource } from "@/lib/types";

type DiscoveredTable = Awaited<ReturnType<typeof api.selection.discover>>[number];
type ActiveRun = Awaited<ReturnType<typeof api.profiling.active>>;

function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const sourceId = params.id;

  const [source, setSource] = useState<DataSource | null>(null);
  const [tables, setTables] = useState<SemTableRow[]>([]);
  const [runs, setRuns] = useState<ProfilingRun[]>([]);
  const [activeRun, setActiveRun] = useState<ActiveRun>(null);
  const [starting, setStarting] = useState(false);
  const [confirmRerun, setConfirmRerun] = useState(false);

  const [discovered, setDiscovered] = useState<DiscoveredTable[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [selectionPicker, setSelectionPicker] = useState(false);
  const [pendingSelection, setPendingSelection] = useState<Set<string>>(new Set());
  // How many tables are currently selected for indexing (null = still loading).
  // Drives the start flow so the user is guided to select instead of hitting an
  // error after clicking "Запустить".
  const [selectedCount, setSelectedCount] = useState<number | null>(null);
  // True when the picker was opened by clicking "Запустить" with no selection —
  // saving then immediately starts profiling.
  const [startAfterSave, setStartAfterSave] = useState(false);

  async function refresh() {
    try {
      const [s, ts, rs, active, selection] = await Promise.all([
        api.sources.get(sourceId),
        api.tables.listForSource(sourceId),
        api.profiling.listRuns(sourceId),
        api.profiling.active(sourceId),
        api.selection.get(sourceId),
      ]);
      setSource(s);
      setTables(ts);
      setRuns(rs);
      setActiveRun(active);
      setSelectedCount(selection.length);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  useEffect(() => {
    refresh();
  }, [sourceId]);

  // While a run is active and attached, poll every 3s so the badge auto-flips
  // to "profiled" without requiring a manual refresh.
  useEffect(() => {
    if (source?.profiling_status !== "in_progress") return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source?.profiling_status]);

  async function openPicker() {
    setDiscovering(true);
    try {
      const list = await api.selection.discover(sourceId);
      setDiscovered(list);
      setPendingSelection(
        new Set(list.filter((t) => t.selected).map((t) => `${t.database}.${t.table}`)),
      );
      setSelectionPicker(true);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка discover");
    } finally {
      setDiscovering(false);
    }
  }

  async function savePicker(thenStart = false) {
    if (!discovered) return;
    setSaving(true);
    try {
      const items = discovered
        .filter((t) => pendingSelection.has(`${t.database}.${t.table}`))
        .map((t) => ({ database: t.database, table: t.table }));
      await api.selection.save(sourceId, items);
      setSelectedCount(items.length);
      setSelectionPicker(false);
      setStartAfterSave(false);
      if (thenStart) {
        // Selection → profiling in one continuous flow (no dead-end error).
        toast.success(`Сохранено ${items.length} таблиц — запускаю профилирование`);
        await performStart();
      } else {
        toast.success(`Сохранено ${items.length} таблиц`);
        await refresh();
      }
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  }

  function toggle(qname: string) {
    setPendingSelection((prev) => {
      const next = new Set(prev);
      if (next.has(qname)) next.delete(qname);
      else next.add(qname);
      return next;
    });
  }

  function toggleAll(filtered: DiscoveredTable[], on: boolean) {
    setPendingSelection((prev) => {
      const next = new Set(prev);
      for (const t of filtered) {
        const qname = `${t.database}.${t.table}`;
        if (on) next.add(qname);
        else next.delete(qname);
      }
      return next;
    });
  }

  const filteredDiscovered = useMemo(() => {
    if (!discovered) return [];
    const q = search.trim().toLowerCase();
    if (!q) return discovered;
    return discovered.filter(
      (t) =>
        t.database.toLowerCase().includes(q) || t.table.toLowerCase().includes(q),
    );
  }, [discovered, search]);

  async function performStart() {
    setStarting(true);
    setConfirmRerun(false);
    try {
      const res = await api.profiling.start(sourceId);
      if (res.reused) {
        toast.info("Профилирование уже идёт — перехожу к нему");
      }
      router.push(`/admin/sources/${sourceId}/runs/${res.run_id}`);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка запуска");
    } finally {
      setStarting(false);
    }
  }

  function onStartClick() {
    if (activeRun && activeRun.run_id) {
      router.push(`/admin/sources/${sourceId}/runs/${activeRun.run_id}`);
      return;
    }
    // No tables selected yet → guide into selection instead of failing. The
    // picker's "Сохранить и запустить" closes the loop.
    if (selectedCount === 0) {
      toast.info("Сначала выберите таблицы — затем сразу запущу профилирование");
      setStartAfterSave(true);
      openPicker();
      return;
    }
    if (source?.profiling_status === "profiled") {
      setConfirmRerun(true);
      return;
    }
    performStart();
  }

  const status = source?.profiling_status ?? "never_profiled";
  const isActive = status === "in_progress" && activeRun !== null;
  const startLabel = isActive
    ? "Перейти к запущенному"
    : selectedCount === 0
      ? "Выбрать таблицы и запустить"
      : status === "profiled"
        ? "Запустить заново"
        : status === "failed"
          ? "Повторить профилирование"
          : "Запустить профилирование";

  return (
    <div className="space-y-6">
      <Link
        href="/admin"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" /> Источники
      </Link>
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          {source ? (
            <>
              <h1 className="animate-fade-in text-2xl font-semibold tracking-tight">
                {source.name}
              </h1>
              <p className="text-sm text-muted-foreground">
                {source.kind} · {source.host}:{source.port}/{source.database}
              </p>
              <div className="flex items-center gap-2">
                <ProfilingStatusBadge
                  status={status}
                  lastProfiledAt={source.last_profiled_at}
                />
                {selectedCount !== null &&
                  (selectedCount > 0 ? (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                      выбрано таблиц: <b className="text-foreground">{selectedCount}</b>
                    </span>
                  ) : (
                    <span className="rounded-full bg-warning/10 px-2 py-0.5 text-xs text-warning">
                      таблицы не выбраны
                    </span>
                  ))}
                {isActive && activeRun?.run_id && (
                  <Link
                    href={`/admin/sources/${sourceId}/runs/${activeRun.run_id}`}
                    className="text-xs text-primary underline"
                  >
                    открыть текущий запуск
                  </Link>
                )}
              </div>
            </>
          ) : (
            <div className="space-y-2">
              <Skeleton className="h-8 w-56" />
              <Skeleton className="h-4 w-72" />
              <Skeleton className="h-6 w-40 rounded-full" />
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          {source ? (
            <>
              <EditSourceDialog source={source} onUpdated={(s) => setSource(s)} />
              <Link href={`/admin/sources/${sourceId}/chat`}>
                <Button variant="outline">Чат с агентом</Button>
              </Link>
              <Tooltip label="Выбрать, какие таблицы индексировать и показывать агенту">
                <Button
                  onClick={() => {
                    setStartAfterSave(false);
                    openPicker();
                  }}
                  variant="outline"
                  disabled={discovering || isActive}
                >
                  {discovering ? "…" : selectedCount ? "Изменить выбор" : "Выбрать таблицы"}
                </Button>
              </Tooltip>
              <Button
                onClick={onStartClick}
                disabled={starting || discovering || selectedCount === null}
              >
                {starting ? "Запуск…" : startLabel}
              </Button>
            </>
          ) : (
            <>
              <Skeleton className="h-10 w-28" />
              <Skeleton className="h-10 w-40" />
            </>
          )}
        </div>
      </div>

      {confirmRerun && (
        <Card className="animate-fade-in-up border-warning/40 bg-warning/5">
          <CardHeader>
            <CardTitle className="text-base">Перезапустить профилирование?</CardTitle>
            <CardDescription>
              Источник уже профилирован {source?.last_profiled_at ? `(${new Date(source.last_profiled_at).toLocaleString("ru-RU")})` : ""}.
              Повторный запуск перезапишет описания таблиц, колонок и связей в семантическом слое и графе.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex gap-2">
            <Button onClick={performStart} disabled={starting}>
              Да, запустить
            </Button>
            <Button variant="ghost" onClick={() => setConfirmRerun(false)}>
              Отмена
            </Button>
          </CardContent>
        </Card>
      )}

      {selectionPicker && discovered && (
        <Card className="animate-fade-in-up">
          <CardHeader>
            <CardTitle className="text-base">
              {startAfterSave
                ? "Шаг 1: выберите таблицы — затем запустим профилирование"
                : "Выберите таблицы для индексации"}
            </CardTitle>
            <CardDescription>
              Профилирование пройдёт только по выбранным. Этот же набор будет
              виден клиентскому пайплайну при генерации SQL.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <Input
                placeholder="Поиск по имени или БД…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="max-w-sm"
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => toggleAll(filteredDiscovered, true)}
              >
                Всё ({filteredDiscovered.length})
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => toggleAll(filteredDiscovered, false)}
              >
                Снять
              </Button>
              <span className="ml-auto text-sm text-muted-foreground">
                Выбрано: <b>{pendingSelection.size}</b> из {discovered.length}
              </span>
            </div>
            <div className="max-h-[420px] overflow-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted text-xs">
                  <tr>
                    <th className="w-8 p-2"></th>
                    <th className="p-2 text-left">База</th>
                    <th className="p-2 text-left">Таблица</th>
                    <th className="p-2 text-left">Engine</th>
                    <th className="p-2 text-right">Строк</th>
                    <th className="p-2 text-right">Размер</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDiscovered.map((t) => {
                    const qname = `${t.database}.${t.table}`;
                    const checked = pendingSelection.has(qname);
                    return (
                      <tr
                        key={qname}
                        className={
                          "border-t hover:bg-muted/40 " +
                          (checked ? "bg-brand-50/40" : "")
                        }
                        onClick={() => toggle(qname)}
                      >
                        <td className="p-2">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(qname)}
                            onClick={(e) => e.stopPropagation()}
                          />
                        </td>
                        <td className="p-2 text-muted-foreground">{t.database}</td>
                        <td className="p-2 font-mono">{t.table}</td>
                        <td className="p-2 text-xs text-muted-foreground">
                          {t.engine || "—"}
                        </td>
                        <td className="p-2 text-right font-mono text-xs">
                          {t.total_rows?.toLocaleString("ru-RU") ?? "—"}
                        </td>
                        <td className="p-2 text-right font-mono text-xs">
                          {formatBytes(t.total_bytes)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  setSelectionPicker(false);
                  setStartAfterSave(false);
                }}
              >
                Отмена
              </Button>
              {startAfterSave ? (
                <>
                  <Button
                    variant="outline"
                    onClick={() => savePicker(false)}
                    disabled={saving || pendingSelection.size === 0}
                  >
                    Только сохранить
                  </Button>
                  <Button
                    onClick={() => savePicker(true)}
                    disabled={saving || pendingSelection.size === 0}
                  >
                    {saving ? "…" : `Сохранить и запустить (${pendingSelection.size})`}
                  </Button>
                </>
              ) : (
                <Button
                  onClick={() => savePicker(false)}
                  disabled={saving || pendingSelection.size === 0}
                >
                  {saving ? "Сохранение…" : "Сохранить выбор"}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {source && <GlossaryEditor source={source} onUpdated={setSource} />}

      <section>
        <h2 className="mb-3 text-lg font-medium">Запуски профилирования</h2>
        {runs.length === 0 && (
          <p className="text-sm text-muted-foreground">Ещё не запускались.</p>
        )}
        <ul className="space-y-2">
          {runs.slice(0, 5).map((r) => (
            <li key={r.id} className="rounded-md border bg-card p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs">{r.id}</span>
                <span className="text-muted-foreground">{r.status}</span>
              </div>
              {r.started_at && (
                <div className="text-xs text-muted-foreground">{r.started_at}</div>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">
          Таблицы в семантическом слое ({tables.length})
        </h2>
        {tables.length === 0 && (
          <p className="text-sm text-muted-foreground">
            Пусто. Выберите таблицы и запустите профилирование.
          </p>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {tables.map((t, i) => (
            <Link
              key={t.id}
              href={`/admin/sources/${sourceId}/tables/${t.id}`}
              className="animate-fade-in-up"
              style={{ animationDelay: `${Math.min(i * 40, 300)}ms` }}
            >
              <Card className="h-full cursor-pointer transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/50 hover:shadow-md">
                <CardHeader>
                  <CardTitle className="text-base">
                    {t.title || `${t.database}.${t.table_name}`}
                  </CardTitle>
                  <CardDescription>
                    {t.database}.{t.table_name} · {t.domain || "—"}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <p className="line-clamp-3 text-sm text-muted-foreground">
                    {t.description || "Без описания"}
                  </p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
