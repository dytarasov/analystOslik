"use client";

import { Pencil } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, HttpError } from "@/lib/api";
import type { DataSource, DataSourceUpdate } from "@/lib/types";

export function EditSourceDialog({
  source,
  onUpdated,
}: {
  source: DataSource;
  onUpdated: (s: DataSource) => void;
}) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [form, setForm] = useState({
    name: source.name,
    host: source.host,
    port: source.port,
    database: source.database,
    username: source.username,
    password: "",
    secure: source.secure,
  });

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  // Reset the form to the current source each time the dialog opens.
  function onOpenChange(next: boolean) {
    if (next) {
      setForm({
        name: source.name,
        host: source.host,
        port: source.port,
        database: source.database,
        username: source.username,
        password: "",
        secure: source.secure,
      });
    }
    setOpen(next);
  }

  function buildPayload(): DataSourceUpdate {
    const payload: DataSourceUpdate = {
      name: form.name,
      host: form.host,
      port: form.port,
      database: form.database,
      username: form.username,
      secure: form.secure,
    };
    if (form.password) payload.password = form.password;
    return payload;
  }

  async function onTest() {
    if (!form.password) {
      toast.info("Введите пароль, чтобы проверить подключение");
      return;
    }
    setTesting(true);
    try {
      const res = await api.sources.testCredentials({
        name: form.name,
        host: form.host,
        port: form.port,
        database: form.database,
        username: form.username,
        password: form.password,
        secure: form.secure,
      });
      if (res.ok) toast.success(`OK · v${res.version} · readonly=${res.readonly}`);
      else toast.error(res.error || "Не удалось подключиться");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setTesting(false);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await api.sources.update(source.id, buildPayload());
      onUpdated(updated);
      toast.success("Источник обновлён");
      setOpen(false);
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-1.5">
          <Pencil className="h-4 w-4" /> Изменить
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Редактирование источника</DialogTitle>
          <DialogDescription>
            Пароль оставьте пустым, чтобы не менять. При смене параметров
            подключения статус read-only сбросится до повторной проверки.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="e-name">Название</Label>
            <Input
              id="e-name"
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
              required
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="e-host">Host</Label>
              <Input
                id="e-host"
                value={form.host}
                onChange={(e) => update("host", e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="e-port">Port</Label>
              <Input
                id="e-port"
                type="number"
                value={form.port}
                onChange={(e) => update("port", Number(e.target.value))}
                required
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="e-db">Database</Label>
            <Input
              id="e-db"
              value={form.database}
              onChange={(e) => update("database", e.target.value)}
              required
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="e-user">Username</Label>
              <Input
                id="e-user"
                value={form.username}
                onChange={(e) => update("username", e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="e-pass">Password</Label>
              <Input
                id="e-pass"
                type="password"
                placeholder="оставьте пустым — без изменений"
                value={form.password}
                onChange={(e) => update("password", e.target.value)}
              />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input
              id="e-secure"
              type="checkbox"
              checked={form.secure}
              onChange={(e) => update("secure", e.target.checked)}
            />
            <Label htmlFor="e-secure">HTTPS</Label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onTest} disabled={testing}>
              {testing ? "Проверка…" : "Тест подключения"}
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Сохранение…" : "Сохранить"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
