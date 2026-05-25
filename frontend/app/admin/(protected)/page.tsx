"use client";

import { Database, Plus, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ProfilingStatusBadge } from "@/components/admin/ProfilingStatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DataSource } from "@/lib/types";

export default function AdminDashboardPage() {
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState<string | null>(null);

  useEffect(() => {
    api.sources
      .list()
      .then(setSources)
      .catch((err) => {
        const msg = err instanceof HttpError ? err.payload.message : "Ошибка загрузки";
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  }, []);

  async function onTest(id: string) {
    setTestingId(id);
    try {
      const res = await api.sources.testConnection(id);
      if (res.ok) {
        toast.success(`OK · v${res.version} · readonly=${res.readonly}`);
      } else {
        toast.error(res.error || "Ошибка соединения");
      }
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setTestingId(null);
    }
  }

  async function onDelete(id: string) {
    if (!confirm("Удалить источник?")) return;
    try {
      await api.sources.remove(id);
      setSources((prev) => (prev || []).filter((s) => s.id !== id));
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Источники данных</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Подключённые базы ClickHouse и их семантический слой
          </p>
        </div>
        <Link href="/admin/sources/new">
          <Button className="gap-1.5">
            <Plus className="h-4 w-4" /> Добавить источник
          </Button>
        </Link>
      </div>

      {loading && (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1].map((i) => (
            <Card key={i}>
              <CardContent className="space-y-3 py-5">
                <Skeleton className="h-5 w-1/3" />
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-6 w-28 rounded-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {sources && sources.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
              <Database className="h-6 w-6 text-primary" />
            </div>
            <p className="text-muted-foreground">
              Источников пока нет. Добавьте первый, чтобы начать.
            </p>
            <Link href="/admin/sources/new">
              <Button variant="outline" className="gap-1.5">
                <Plus className="h-4 w-4" /> Добавить источник
              </Button>
            </Link>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {(sources || []).map((s, i) => (
          <Link
            key={s.id}
            href={`/admin/sources/${s.id}`}
            className="group relative block animate-fade-in-up rounded-xl"
            style={{ animationDelay: `${Math.min(i * 50, 300)}ms` }}
          >
            <Card className="h-full transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md">
              <CardContent className="space-y-3 py-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="truncate font-semibold group-hover:text-primary">
                      {s.name}
                    </h3>
                    <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                      {s.host}:{s.port}/{s.database}
                    </p>
                  </div>
                  {/* Action cluster — stop propagation so it never navigates. */}
                  <div
                    className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                  >
                    <Tooltip label="Проверить подключение">
                      <button
                        type="button"
                        onClick={() => onTest(s.id)}
                        className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        <RefreshCw
                          className={cn(
                            "h-4 w-4",
                            testingId === s.id && "animate-spin text-primary",
                          )}
                        />
                      </button>
                    </Tooltip>
                    <Tooltip label="Удалить источник">
                      <button
                        type="button"
                        onClick={() => onDelete(s.id)}
                        className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </Tooltip>
                  </div>
                </div>

                <ProfilingStatusBadge
                  status={s.profiling_status}
                  lastProfiledAt={s.last_profiled_at}
                />
                <div className="text-xs text-muted-foreground">
                  Тест: {s.last_test_status || "не выполнялся"}
                  {s.readonly_verified ? " · read-only ✓" : ""}
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
