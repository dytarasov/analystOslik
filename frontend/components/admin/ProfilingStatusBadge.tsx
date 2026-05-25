import type { ProfilingStatus } from "@/lib/types";

type Props = {
  status: ProfilingStatus;
  lastProfiledAt?: string | null;
};

const META: Record<
  ProfilingStatus,
  { label: string; className: string }
> = {
  never_profiled: {
    label: "Не профилирован",
    className: "border-muted-foreground/30 bg-muted text-muted-foreground",
  },
  in_progress: {
    label: "Идёт профилирование",
    className: "border-warning/40 bg-warning/10 text-warning",
  },
  profiled: {
    label: "Профилирован",
    className: "border-success/40 bg-success/10 text-success",
  },
  failed: {
    label: "Ошибка профилирования",
    className: "border-destructive/40 bg-destructive/10 text-destructive",
  },
  stale: {
    label: "Устарел",
    className: "border-warning/40 bg-warning/10 text-warning",
  },
};

function formatRelative(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ProfilingStatusBadge({ status, lastProfiledAt }: Props) {
  const meta = META[status];
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium " +
        meta.className
      }
      title={lastProfiledAt ? `Последний успех: ${formatRelative(lastProfiledAt)}` : undefined}
    >
      <span
        className={
          "h-1.5 w-1.5 rounded-full bg-current " +
          (status === "in_progress" ? "animate-pulse-soft" : "opacity-70")
        }
      />
      {meta.label}
      {status === "profiled" && lastProfiledAt && (
        <span className="ml-1 font-normal opacity-70">
          · {formatRelative(lastProfiledAt)}
        </span>
      )}
    </span>
  );
}
