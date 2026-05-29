"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";

import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, HttpError } from "@/lib/api";

export default function UnlockPage() {
  // useSearchParams требует Suspense, иначе next build падает на пререндере.
  return (
    <Suspense>
      <UnlockForm />
    </Suspense>
  );
}

function UnlockForm() {
  const params = useSearchParams();
  // Только относительные пути — защита от open-redirect через ?next=.
  const rawNext = params.get("next") || "/";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";
  const [key, setKey] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await api.access.unlock(key.trim());
      // Жёсткая навигация: свежий cookie доступа должен уйти в middleware.
      window.location.replace(next);
    } catch (err) {
      const msg = err instanceof HttpError ? err.payload.message : "Не удалось проверить ключ";
      toast.error(msg);
      setLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center gap-6 px-4">
      <div className="terminal-grid terminal-grid-fade pointer-events-none absolute inset-0 -z-10" />
      <div className="flex flex-col items-center gap-3">
        <DonkeyMark size={56} rounded="rounded-lg" />
        <div className="text-center leading-tight">
          <div className="font-mono text-lg font-semibold tracking-tight">Аналитический Ослик</div>
          <div className="mt-1.5 font-sans text-xs text-muted-foreground">спросите данные словами</div>
        </div>
      </div>
      <Card className="brackets w-full max-w-sm">
        <CardHeader>
          <span className="label-mono">доступ</span>
          <CardTitle className="pt-1">Введите ключ доступа</CardTitle>
          <CardDescription>
            Сервис закрыт. Вставьте выданный вам UUID-ключ, чтобы продолжить.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="key" className="label-mono">ключ</Label>
              <Input
                id="key"
                className="font-mono"
                placeholder="00000000-0000-0000-0000-000000000000"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                autoFocus
                autoComplete="off"
                spellCheck={false}
                required
              />
            </div>
            <Button type="submit" disabled={loading || !key.trim()} className="w-full font-mono">
              {loading ? "Проверяю…" : "Войти →"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
