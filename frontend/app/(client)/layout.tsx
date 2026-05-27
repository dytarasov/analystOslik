"use client";

import { PanelLeftClose, PanelLeftOpen, Plus, Settings, Trash2 } from "lucide-react";
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

const SIDEBAR_KEY = "t2r:sidebar-collapsed";

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [collapsed, setCollapsed] = useState(false);

  // Restore the collapsed preference; with no saved choice, default to collapsed
  // on narrow screens so the chat gets the full width on phones.
  useEffect(() => {
    const stored = localStorage.getItem(SIDEBAR_KEY);
    if (stored != null) {
      setCollapsed(stored === "1");
      return;
    }
    setCollapsed(window.matchMedia("(max-width: 767px)").matches);
  }, []);

  function toggleSidebar() {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
      } catch {
        /* noop */
      }
      return next;
    });
  }

  // On mobile the sidebar is an overlay drawer — close it after navigating so
  // the chat is visible. We don't persist this (it's not a desktop preference).
  function closeOnMobile() {
    if (typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches) {
      setCollapsed(true);
    }
  }

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
    <div
      className="relative h-screen md:grid md:transition-[grid-template-columns] md:duration-300 md:ease-in-out md:[grid-template-columns:var(--sb)_1fr]"
      // --sb drives the sidebar column width; --chat-max lets the chat content
      // reclaim some of the freed space when the sidebar is collapsed (instead
      // of leaving a wide empty gutter), and the chat animates between the two.
      style={
        {
          "--sb": collapsed ? "0px" : "272px",
          "--chat-max": collapsed ? "78rem" : "62rem",
        } as React.CSSProperties
      }
    >
      {/* Mobile-only backdrop while the drawer is open. */}
      {!collapsed && (
        <button
          aria-label="Закрыть панель"
          onClick={toggleSidebar}
          className="fixed inset-0 z-30 bg-foreground/40 backdrop-blur-sm md:hidden"
        />
      )}
      <aside
        className={cn(
          "z-40 min-h-0 overflow-hidden bg-card",
          // Mobile: a fixed drawer that slides in/out. Desktop: a static grid
          // cell whose width the grid animates (inner 272px clipped when 0).
          "fixed inset-y-0 left-0 transition-transform duration-300 md:static md:translate-x-0 md:transition-none",
          collapsed ? "-translate-x-full md:translate-x-0" : "translate-x-0",
        )}
      >
        <div className="flex h-full w-[272px] min-h-0 flex-col border-r">
        <div className="flex items-center gap-2.5 border-b border-dashed px-4 py-4">
          <DonkeyMark size={36} />
          <div className="min-w-0 flex-1 leading-tight">
            <Link href="/" className="block truncate font-mono text-sm font-semibold tracking-tight">
              Аналитический Ослик
            </Link>
            <span className="mt-1 block font-sans text-[11px] text-muted-foreground">спросите данные словами</span>
          </div>
          <Tooltip label="Свернуть панель" side="right">
            <button
              onClick={toggleSidebar}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="Свернуть панель"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>
          </Tooltip>
        </div>

        <div className="px-3 pb-2 pt-3">
          <span className="label-mono mb-2 block px-1">сессии</span>
          <button
            onClick={() => {
              onNewChat();
              closeOnMobile();
            }}
            className={cn(
              "flex w-full items-center gap-2 rounded-md border px-3 py-2 font-mono text-sm font-medium transition-all",
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
            <p className="px-3 py-6 text-center font-mono text-xs text-muted-foreground">
              — пусто —
            </p>
          )}
          <ul className="space-y-0.5">
            {sessions.map((s) => {
              const active = s.id === activeId;
              return (
                <li key={s.id} className="group relative">
                  <Link
                    href={`/chat/${s.id}`}
                    onClick={closeOnMobile}
                    className={cn(
                      "flex items-center gap-2 rounded-md py-2 pl-3 pr-8 text-sm transition-colors",
                      active
                        ? "bg-primary/10 text-foreground"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {active && (
                      <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 bg-primary" />
                    )}
                    <span
                      className={cn(
                        "shrink-0 font-mono text-xs",
                        active ? "text-primary" : "text-muted-foreground/60",
                      )}
                      aria-hidden
                    >
                      {active ? "›" : "·"}
                    </span>
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
          className="flex items-center gap-2 border-t border-dashed px-4 py-3 font-mono text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Settings className="h-3.5 w-3.5" /> admin →
        </Link>
        </div>
      </aside>

      <main className="relative flex h-full min-h-0 flex-col overflow-hidden">{children}</main>

      {/* Reopen control — a top-left pill (donkey + «сессии») that sits in the
          chat header band where the status text used to be. Only when collapsed. */}
      <button
        onClick={toggleSidebar}
        aria-label="Развернуть панель сессий"
        className={cn(
          "group fixed left-4 top-2.5 z-50 flex items-center gap-2 rounded-md border bg-card/90 py-1 pl-1 pr-3 shadow-md backdrop-blur transition-all duration-200 hover:border-primary/40 hover:bg-card",
          collapsed
            ? "translate-x-0 opacity-100"
            : "pointer-events-none -translate-x-2 opacity-0",
        )}
      >
        <DonkeyMark size={24} rounded="rounded" />
        <span className="font-mono text-sm font-medium tracking-tight">сессии</span>
        <PanelLeftOpen className="h-4 w-4 text-muted-foreground transition-all group-hover:translate-x-0.5 group-hover:text-primary" />
      </button>
    </div>
  );
}
