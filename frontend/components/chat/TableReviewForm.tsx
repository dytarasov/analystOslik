"use client";

import { Check, SkipForward } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

export type TableReview = {
  database?: string;
  table?: string;
  title?: string;
  description?: string;
  domain?: string;
  grain?: string;
  columns?: { name: string; type: string }[];
};

export function TableReviewForm({
  review,
  onSubmit,
}: {
  review: TableReview;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const [showCols, setShowCols] = useState(false);

  // Reset the draft when a new table comes up for review.
  useEffect(() => {
    setText("");
    setShowCols(false);
  }, [review.database, review.table]);

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Проверьте черновик описания. Добавьте правки/уточнения текстом — они
        учтутся при перегенерации и сохранятся как комментарий. Или пропустите.
      </p>

      {review.title && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{review.title}</span>
          {review.domain && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              {review.domain}
            </span>
          )}
        </div>
      )}
      {review.grain && (
        <div className="text-xs text-muted-foreground">
          Грануляр­ность: {review.grain}
        </div>
      )}
      {review.description && (
        <div className="whitespace-pre-wrap rounded-lg border bg-muted/30 p-3 text-sm leading-relaxed">
          {review.description}
        </div>
      )}

      {review.columns && review.columns.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowCols((v) => !v)}
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            {showCols ? "Скрыть" : "Показать"} колонки ({review.columns.length})
          </button>
          {showCols && (
            <div className="mt-2 flex flex-wrap gap-1">
              {review.columns.map((c) => (
                <span
                  key={c.name}
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
                >
                  {c.name}
                  <span className="text-muted-foreground">:{c.type}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.stopPropagation()}
        placeholder="Ваши правки/уточнения по таблице (или пропустите)…"
        className="min-h-[90px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />

      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onSubmit("")} className="gap-1.5">
          <SkipForward className="h-4 w-4" /> Пропустить
        </Button>
        <Button
          onClick={() => onSubmit(text)}
          disabled={!text.trim()}
          className="gap-1.5"
        >
          <Check className="h-4 w-4" /> Применить и продолжить
        </Button>
      </div>
    </div>
  );
}
