"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, HttpError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/admin";
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await api.auth.login(login, password);
      router.replace(next);
    } catch (err) {
      const msg = err instanceof HttpError ? err.payload.message : "Ошибка входа";
      toast.error(msg);
    } finally {
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
          <span className="label-mono">авторизация</span>
          <CardTitle className="pt-1">Вход для администратора</CardTitle>
          <CardDescription>Введите логин и пароль из .env</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="login" className="label-mono">логин</Label>
              <Input
                id="login"
                className="font-mono"
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                autoFocus
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password" className="label-mono">пароль</Label>
              <Input
                id="password"
                type="password"
                className="font-mono"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" disabled={loading} className="w-full font-mono">
              {loading ? "Вход…" : "Войти →"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
