"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { AgentStatusTimeline } from "@/components/chat/AgentStatusTimeline";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTask } from "@/hooks/useTask";
import { api, HttpError } from "@/lib/api";

export default function AdminChatPage() {
  const params = useParams<{ id: string }>();
  const [prompt, setPrompt] = useState("");
  const task = useTask();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    try {
      const { agent_run_id } = await api.adminEdit.submit(params.id, prompt);
      task.start(api.adminEdit.eventsUrl(agent_run_id));
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка");
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Команда агенту</h1>
        <p className="text-sm text-muted-foreground">
          Опишите что нужно изменить в семантическом слое. Например: «уточни описание таблицы orders с учётом того,
          что это магазин подписок» или «добавь связь orders.user_id → users.id».
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-3">
        <textarea
          className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Опишите задачу…"
        />
        <Button type="submit" disabled={task.state === "running" || task.state === "connecting"}>
          {task.state === "running" ? "Выполняю…" : "Запустить"}
        </Button>
      </form>

      {task.steps.length > 0 && <AgentStatusTimeline steps={task.steps} />}

      {task.result && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Результат</CardTitle>
          </CardHeader>
          <CardContent>
            <p>{task.result.summary}</p>
            <pre className="mt-3 overflow-x-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(task.result.preview, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {task.errorMsg && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {task.errorMsg}
        </div>
      )}
    </div>
  );
}
