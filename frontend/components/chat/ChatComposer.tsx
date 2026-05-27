"use client";

import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ChatComposer({
  onSend,
  disabled,
  placeholder = "Опишите задачу человеческим языком…",
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea with its content (up to the CSS max-height), so a
  // multi-line prompt is fully visible instead of cramped into one scrolling row.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText("");
    // Collapse back to a single row after sending.
    requestAnimationFrame(() => {
      if (taRef.current) taRef.current.style.height = "auto";
    });
  }

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t bg-card/80 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-[var(--chat-max,48rem)] items-end gap-2 rounded-md border bg-background p-2 pl-3 shadow-sm transition-all focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/25 focus-within:shadow-glow">
        <span
          aria-hidden
          className={cn(
            "select-none self-start py-1.5 font-mono text-sm leading-relaxed transition-colors",
            disabled ? "text-muted-foreground/40" : "text-primary/70",
          )}
        >
          &gt;
        </span>
        <textarea
          ref={taRef}
          className="flex max-h-[200px] min-h-[36px] w-full resize-none bg-transparent px-1 py-1.5 text-sm leading-relaxed focus-visible:outline-none disabled:cursor-not-allowed"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
        />
        <Button
          type="submit"
          disabled={!text.trim() || disabled}
          size="icon"
          className="h-9 w-9 shrink-0 rounded-md transition-transform hover:scale-105 active:scale-95"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
      <div className="mx-auto mt-1.5 max-w-[var(--chat-max,48rem)] px-3 font-mono text-[10px] text-muted-foreground">
        ENTER — отправить · SHIFT+ENTER — перенос
      </div>
    </form>
  );
}
