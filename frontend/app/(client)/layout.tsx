"use client";

import { MessageSquare, Plus, Settings, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { Tooltip } from "@/components/ui/tooltip";
import { api, HttpError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Session = {
  id: string;
  title: string | null;
  last_activity_at: string;
  source_id: string | null;
};

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);

  async function refresh() {
    try {
      const res = await api.client.listSessions();
      setSessions(res.items);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    refresh();
  }, [pathname]);

  useEffect(() => {
    const onChanged = () => refresh();
    window.addEventListener("t2r:sessions-changed", onChanged);
    return () => window.removeEventListener("t2r:sessions-changed", onChanged);
  }, []);

  const activeId = pathname?.startsWith("/chat/") ? pathname.split("/")[2] ?? null : null;
  const onHome = pathname === "/";

  function onNewChat() {
    // On a real /chat/{id} route, navigate home (fresh mount). When already on
    // "/", router.push is a no-op, so tell ChatRunner to wipe its state.
    if (pathname !== "/") {
      router.push("/");
    } else {
      window.dispatchEvent(new Event("t2r:new-chat"));
    }
  }

  async function onDelete(id: string) {
    if (!confirm("Удалить этот чат?")) return;
    try {
      await api.client.deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (id === activeId) router.push("/");
    } catch (err) {
      toast.error(err instanceof HttpError ? err.payload.message : "Ошибка удаления");
    }
  }

  return (
    <div className="grid h-screen grid-cols-[268px_1fr]">
      <aside className="flex min-h-0 flex-col border-r bg-card">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <DonkeyMark size={36} />
          <div className="leading-tight">
            <Link href="/" className="block font-semibold tracking-tight">
              Аналитический Ослик
            </Link>
            <span className="text-[11px] text-muted-foreground">спросите данные словами</span>
          </div>
        </div>

        <div className="px-3 pb-2">
          <button
            onClick={onNewChat}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-all",
              onHome
                ? "border-primary/40 bg-primary/10 text-primary"
                : "border-border bg-background hover:border-primary/40 hover:bg-primary/5 hover:text-primary",
            )}
          >
            <Plus className="h-4 w-4" /> Новый чат
          </button>
        </div>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {sessions.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              Чатов пока нет
            </p>
          )}
          <ul className="space-y-0.5">
            {sessions.map((s) => {
              const active = s.id === activeId;
              return (
                <li key={s.id} className="group relative">
                  <Link
                    href={`/chat/${s.id}`}
                    className={cn(
                      "flex items-center gap-2 rounded-lg py-2 pl-3 pr-8 text-sm transition-colors",
                      active
                        ? "bg-primary/10 text-foreground"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {active && (
                      <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-brand-gradient" />
                    )}
                    <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-70" />
                    <span className="min-w-0 flex-1 truncate" title={s.title || s.id}>
                      {s.title || "Без названия"}
                    </span>
                  </Link>
                  <Tooltip label="Удалить чат" side="right">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        onDelete(s.id);
                      }}
                      className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground opacity-0 transition hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </Tooltip>
                </li>
              );
            })}
          </ul>
        </div>

        <Link
          href="/admin"
          className="flex items-center gap-2 border-t px-4 py-3 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Settings className="h-3.5 w-3.5" /> Админка
        </Link>
      </aside>
      <main className="flex min-h-0 flex-col overflow-hidden">{children}</main>
    </div>
  );
}
