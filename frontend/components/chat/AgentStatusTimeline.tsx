"use client";

import { Check, ChevronDown, ChevronRight, CircleAlert, Loader2, Sparkles } from "lucide-react";
import { useState } from "react";

import type { StepInfo } from "@/hooks/useTask";
import { cn } from "@/lib/utils";

export function AgentStatusTimeline({
  steps,
  collapsible = false,
  defaultCollapsed = false,
}: {
  steps: StepInfo[];
  collapsible?: boolean;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  if (steps.length === 0) return null;

  const allDone = steps.every((s) => s.status !== "running");
  const totalMs = steps.reduce((acc, s) => acc + (s.durationMs || 0), 0);
  const failedCount = steps.filter((s) => s.status === "failed").length;
  const showCollapsed = collapsible && allDone && collapsed;
  const canToggle = collapsible && allDone;

  return (
    <div
      className={cn(
        "animate-fade-in overflow-hidden rounded-xl border bg-card/70 text-sm shadow-sm backdrop-blur-sm",
        failedCount > 0 && "border-destructive/30 bg-destructive/5",
      )}
    >
      <button
        type="button"
        onClick={() => canToggle && setCollapsed((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left",
          canToggle && "cursor-pointer transition-colors hover:bg-muted/40",
        )}
      >
        {!allDone ? (
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
        ) : failedCount > 0 ? (
          <CircleAlert className="h-4 w-4 text-destructive" />
        ) : (
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-success/15">
            <Check className="h-3 w-3 text-success" />
          </span>
        )}
        <span className="font-mono text-xs font-medium tracking-tight">
          {allDone
            ? failedCount > 0
              ? `ЛОГ · ошибок ${failedCount}`
              : `ГОТОВО · ${steps.length} шаг(ов) · ${(totalMs / 1000).toFixed(1)}с`
            : `РАБОТАЮ · ${steps.filter((s) => s.status !== "running").length}/${steps.length}`}
        </span>
        {canToggle && (
          <span className="ml-auto text-muted-foreground transition-transform">
            {collapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </span>
        )}
      </button>

      {!showCollapsed && (
        <ol className="space-y-0.5 border-t px-3 py-2">
          {steps.map((step, i) => (
            <li
              key={step.id}
              className="flex animate-fade-in-up items-start gap-2.5 py-1 text-xs"
              // While the agent is live, a new step should pop in immediately —
              // the per-item stagger only plays when reviewing a finished run.
              style={{ animationDelay: allDone ? `${Math.min(i * 30, 180)}ms` : "0ms" }}
            >
              <span className="relative mt-0.5 flex shrink-0 flex-col items-center">
                {step.status === "running" && (
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                )}
                {step.status === "completed" && (
                  <Check className="h-4 w-4 text-success" />
                )}
                {step.status === "failed" && (
                  <CircleAlert className="h-4 w-4 text-destructive" />
                )}
                {i < steps.length - 1 && (
                  <span className="absolute top-5 h-[calc(100%-4px)] w-px bg-border" />
                )}
              </span>
              <div className="min-w-0 flex-1 pb-1">
                <div
                  className={cn(
                    "font-mono leading-snug",
                    step.status === "failed" && "text-destructive",
                    step.status === "running" && "text-foreground",
                    step.status === "completed" && "text-muted-foreground",
                  )}
                >
                  {step.name}
                </div>
                {step.detail && (
                  <div className="line-clamp-2 text-[11px] text-muted-foreground">
                    {step.detail}
                  </div>
                )}
                {step.error && (
                  <div className="text-[11px] text-destructive">{step.error}</div>
                )}
              </div>
              {step.durationMs !== undefined && step.status !== "running" && (
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                  {(step.durationMs / 1000).toFixed(1)}с
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
