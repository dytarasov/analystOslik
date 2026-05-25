"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, HttpError } from "@/lib/api";
import type { DataSourceCreate } from "@/lib/types";

export default function NewSourcePage() {
  const router = useRouter();
  const [form, setForm] = useState<DataSourceCreate>({
    name: "",
    host: "clickhouse",
    port: 8123,
    database: "demo",
    username: "demo",
    password: "demo",
    secure: false,
  });
  const [submitting, setSubmitting] = useState(false);
  const [testing, setTesting] = useState(false);

  function update<K extends keyof DataSourceCreate>(key: K, value: DataSourceCreate[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onTest() {
    setTesting(true);
    try {
      const res = await api.sources.testCredentials(form);
      if (res.ok) {
        toast.success(`OK · v${res.version} · readonly=${res.readonly}`);
      } else {
        toast.error(res.error || "Не удалось подключиться");
      }
    } catch (err) {
      const msg = err instanceof HttpError ? err.payload.message : "Ошибка";
      toast.error(msg);
    } finally {
      setTesting(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const created = await api.sources.create(form);
      toast.success(`Источник '${created.name}' создан`);
      router.push("/admin");
    } catch (err) {
      const msg = err instanceof HttpError ? err.payload.message : "Ошибка создания";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <CardHeader>
          <CardTitle>Новый источник ClickHouse</CardTitle>
          <CardDescription>Подключение к серверу будет проверено и записано зашифрованно.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Название</Label>
              <Input id="name" value={form.name} onChange={(e) => update("name", e.target.value)} required />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="host">Host</Label>
                <Input id="host" value={form.host} onChange={(e) => update("host", e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="port">Port</Label>
                <Input
                  id="port"
                  type="number"
                  value={form.port}
                  onChange={(e) => update("port", Number(e.target.value))}
                  required
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="database">Database</Label>
              <Input id="database" value={form.database} onChange={(e) => update("database", e.target.value)} required />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input id="username" value={form.username} onChange={(e) => update("username", e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={form.password}
                  onChange={(e) => update("password", e.target.value)}
                  required
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <input
                id="secure"
                type="checkbox"
                checked={form.secure}
                onChange={(e) => update("secure", e.target.checked)}
              />
              <Label htmlFor="secure">HTTPS</Label>
            </div>
            <div className="flex gap-3 pt-2">
              <Button type="button" variant="outline" onClick={onTest} disabled={testing}>
                {testing ? "Проверка…" : "Тест подключения"}
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? "Сохранение…" : "Создать"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
