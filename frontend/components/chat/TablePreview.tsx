"use client";

import { ChevronDown, ChevronsUpDown, ChevronUp, Download, Maximize2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const nf = new Intl.NumberFormat("ru-RU");

// Identifier columns: numeric-looking but not quantities — right-aligned but
// never grouped with thousand separators (1 234 567 would be misleading).
const ID_LIKE = /(^|_)id$|^id$|uuid|guid|hash/i;
// Phone numbers, registry & postal codes: digit strings that must read as plain
// text — never grouped (8 999 123…), never sorted/rounded as floats.
const CODE_LIKE =
  /phone|тел|mobile|моб|fax|факс|\binn\b|инн|ogrn|огрн|\bkpp\b|кпп|snils|снилс|passport|паспорт|карт|zip|postal|индекс|(^|_)code$|(^|_)код$/i;

// A value is a real quantity (groupable, right-aligned, numerically sorted) only
// if it parses as a number AND isn't identifier-shaped: a leading zero (007), a
// leading + (phone), or more digits than a safe integer — formatting/round-trip
// through Number() would otherwise corrupt it.
function isQuantityCell(v: unknown): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (typeof v !== "string") return false;
  const s = v.trim();
  if (s === "" || Number.isNaN(Number(s))) return false;
  if (/^[+-]?0\d/.test(s)) return false; // 007, -012
  if (s.startsWith("+")) return false; // +7 999…
  if (s.replace(/\D/g, "").length > 15) return false; // beyond safe-integer precision
  return true;
}

function renderCell(v: unknown, grouped: boolean) {
  if (v === null || v === undefined)
    return <span className="text-muted-foreground">∅</span>;
  if (grouped && isQuantityCell(v)) return nf.format(Number(v));
  return String(v);
}

type Sort = { col: number; dir: "asc" | "desc" };

// Shared table body — rendered both inline (truncated) and in the modal (wrapped,
// so long values like full institution names are fully readable). Sorting is
// lifted to the parent so both views stay in sync.
function DataGrid({
  columns,
  rows,
  numericCol,
  groupedCol,
  wrap,
  sort,
  onSort,
}: {
  columns: string[];
  rows: unknown[][];
  numericCol: boolean[];
  groupedCol: boolean[];
  wrap: boolean;
  sort: Sort | null;
  onSort: (col: number) => void;
}) {
  return (
    <table className="w-full border-collapse text-xs">
      <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
        <tr>
          {columns.map((c, j) => {
            const active = sort?.col === j;
            return (
              <th
                key={c}
                aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                className={cn(
                  "whitespace-nowrap border-b border-r border-r-border/60 p-0 font-mono text-[11px] font-semibold tracking-tight last:border-r-0",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSort(j)}
                  title="Сортировать"
                  className={cn(
                    "group flex w-full items-center gap-1 px-3 py-2 transition-colors hover:bg-primary/[0.06]",
                    numericCol[j] ? "justify-end" : "justify-start",
                    active && "text-primary",
                  )}
                >
                  <span className="truncate">{c}</span>
                  {active ? (
                    sort.dir === "asc" ? (
                      <ChevronUp className="h-3 w-3 shrink-0" />
                    ) : (
                      <ChevronDown className="h-3 w-3 shrink-0" />
                    )
                  ) : (
                    <ChevronsUpDown className="h-3 w-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-40" />
                  )}
                </button>
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr
            key={i}
            className={cn(
              "transition-colors hover:bg-primary/[0.06]",
              i % 2 === 1 && "bg-muted/30",
            )}
          >
            {r.map((v, j) => (
              <td
                key={j}
                className={cn(
                  "border-b border-r border-r-border/60 px-3 py-1.5 align-top last:border-r-0",
                  numericCol[j]
                    ? "whitespace-nowrap text-right font-mono tabular-nums"
                    : "text-left font-sans",
                )}
              >
                <div
                  className={cn(
                    numericCol[j]
                      ? "ml-auto"
                      : wrap
                        ? "max-w-[760px] whitespace-normal break-words leading-snug"
                        : "max-w-[340px] truncate",
                  )}
                  title={v === null || v === undefined ? "" : String(v)}
                >
                  {renderCell(v, !!groupedCol[j])}
                </div>
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TablePreview({
  columns,
  rows,
  totalRows,
  exportHref,
}: {
  columns: string[];
  rows: unknown[][];
  totalRows: number;
  exportHref?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [sort, setSort] = useState<Sort | null>(null);

  const numericCol = useMemo(
    () =>
      columns.map(
        (c, j) =>
          !CODE_LIKE.test(c) &&
          rows.length > 0 &&
          rows.every((r) => r[j] === null || r[j] === undefined || isQuantityCell(r[j])),
      ),
    [columns, rows],
  );
  const groupedCol = useMemo(
    () => columns.map((c, j) => !!numericCol[j] && !ID_LIKE.test(c)),
    [columns, numericCol],
  );

  // Click a header to cycle asc → desc → unsorted. Numeric columns compare as
  // numbers, the rest by locale; nulls always sink to the bottom.
  function onSort(col: number) {
    setSort((prev) => {
      if (!prev || prev.col !== col) return { col, dir: "asc" };
      if (prev.dir === "asc") return { col, dir: "desc" };
      return null;
    });
  }

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const { col, dir } = sort;
    const numeric = !!numericCol[col];
    return [...rows].sort((ra, rb) => {
      const a = ra[col];
      const b = rb[col];
      const an = a === null || a === undefined;
      const bn = b === null || b === undefined;
      if (an || bn) return an && bn ? 0 : an ? 1 : -1; // nulls last, both ways
      const base = numeric
        ? Number(a) - Number(b)
        : String(a).localeCompare(String(b), "ru");
      return dir === "asc" ? base : -base;
    });
  }, [rows, sort, numericCol]);

  return (
    <>
      <div className="brackets animate-fade-in overflow-hidden rounded-md border bg-card shadow-sm">
        <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-3 py-1.5">
          <div className="flex items-baseline gap-2">
            <span className="label-mono">результат</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {columns.length}×{totalRows}
            </span>
          </div>
          <div className="flex items-center gap-1">
            {exportHref && (
              <a
                href={exportHref}
                title="Скачать результат в Excel"
                className="inline-flex h-6 items-center gap-1 rounded px-1.5 font-mono text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-primary"
              >
                <Download className="h-3.5 w-3.5" /> XLSX
              </a>
            )}
            <button
              type="button"
              onClick={() => setOpen(true)}
              title="Развернуть на весь экран"
              className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <Maximize2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <div className="scroll-thin max-h-[400px] overflow-auto">
          <DataGrid
            columns={columns}
            rows={sortedRows}
            numericCol={numericCol}
            groupedCol={groupedCol}
            wrap={false}
            sort={sort}
            onSort={onSort}
          />
        </div>
        <div className="border-t px-3 py-2 font-mono text-[11px] text-muted-foreground">
          показано <span className="font-medium text-foreground">{rows.length}</span> / {totalRows}
        </div>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[90vh] w-full max-w-[94vw] flex-col overflow-hidden p-0">
          <DialogTitle className="flex items-baseline gap-2 border-b px-4 py-3 pr-12">
            <span className="label-mono">результат</span>
            <span className="font-mono text-[11px] font-normal text-muted-foreground">
              {columns.length} колонок · {totalRows} строк
            </span>
            {exportHref && (
              <a
                href={exportHref}
                title="Скачать результат в Excel"
                className="ml-auto inline-flex items-center gap-1.5 self-center rounded-md border border-input bg-background px-2.5 py-1 font-mono text-xs transition-colors hover:border-primary/40 hover:bg-accent"
              >
                <Download className="h-3.5 w-3.5 text-primary" /> XLSX
              </a>
            )}
          </DialogTitle>
          <div className="scroll-thin min-h-0 flex-1 overflow-auto">
            <DataGrid
              columns={columns}
              rows={sortedRows}
              numericCol={numericCol}
              groupedCol={groupedCol}
              wrap
              sort={sort}
              onSort={onSort}
            />
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
