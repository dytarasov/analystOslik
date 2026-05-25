import { cn } from "@/lib/utils";

export function TablePreview({
  columns,
  rows,
  totalRows,
}: {
  columns: string[];
  rows: unknown[][];
  totalRows: number;
}) {
  return (
    <div className="animate-fade-in overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="scroll-thin max-h-[400px] overflow-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  className="border-b px-3 py-2 text-left font-semibold tracking-tight"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.map((r, i) => (
              <tr
                key={i}
                className={cn(
                  "transition-colors hover:bg-primary/5",
                  i % 2 === 1 && "bg-muted/30",
                )}
              >
                {r.map((v, j) => (
                  <td key={j} className="border-b px-3 py-1.5 align-top">
                    <div className="max-w-[280px] truncate" title={String(v)}>
                      {v === null ? <span className="text-muted-foreground">∅</span> : String(v)}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t px-3 py-2 text-xs text-muted-foreground">
        Показано <span className="font-medium text-foreground">{rows.length}</span> из {totalRows}
      </div>
    </div>
  );
}
