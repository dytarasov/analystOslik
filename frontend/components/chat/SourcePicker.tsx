"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronsUpDown, Database } from "lucide-react";

import { cn } from "@/lib/utils";

type Source = { id: string; name: string };

export function SourcePicker({
  sources,
  sourceId,
  onChange,
}: {
  sources: Source[];
  sourceId: string | null;
  onChange: (id: string) => void;
}) {
  const current = sources.find((s) => s.id === sourceId) ?? null;

  if (sources.length === 0) {
    return (
      <span className="text-sm text-muted-foreground">
        Нет источников — добавьте в админке
      </span>
    );
  }

  if (sources.length === 1) {
    return (
      <div className="flex items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-sm">
        <Database className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{current?.name}</span>
      </div>
    );
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="flex items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-sm transition-colors hover:border-primary/40 hover:bg-accent">
          <Database className="h-4 w-4 text-muted-foreground" />
          <span className="max-w-[180px] truncate font-medium">{current?.name}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[220px] animate-scale-in rounded-lg border bg-card p-1 shadow-lg"
        >
          <DropdownMenu.Label className="px-2 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Источник данных
          </DropdownMenu.Label>
          {sources.map((s) => (
            <DropdownMenu.Item
              key={s.id}
              onSelect={() => onChange(s.id)}
              className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none transition-colors data-[highlighted]:bg-accent"
            >
              <Check
                className={cn(
                  "h-3.5 w-3.5 text-primary",
                  s.id === sourceId ? "opacity-100" : "opacity-0",
                )}
              />
              <span className="truncate">{s.name}</span>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
