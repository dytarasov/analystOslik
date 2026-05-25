"use client";

import { Send } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText("");
  }

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t bg-card/80 px-4 py-3 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border bg-background p-2 shadow-sm transition-shadow focus-within:border-primary/40 focus-within:shadow-glow">
        <textarea
          className="flex max-h-[200px] min-h-[36px] w-full resize-none bg-transparent px-3 py-1.5 text-sm leading-relaxed focus-visible:outline-none disabled:cursor-not-allowed"
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
          className="h-9 w-9 shrink-0 rounded-xl transition-transform hover:scale-105 active:scale-95"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
      <div className="mx-auto mt-1.5 max-w-3xl px-3 text-[10px] text-muted-foreground">
        Enter — отправить · Shift+Enter — перенос строки
      </div>
    </form>
  );
}
