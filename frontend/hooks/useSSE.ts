"use client";

import { useEffect, useRef, useState } from "react";

import type { AgentEvent } from "@/lib/events";
import { streamSSE } from "@/lib/sse";

export type UseSSEOptions = {
  url: string | null;
  enabled?: boolean;
  onEvent: (event: AgentEvent) => void;
};

export function useSSE({ url, enabled = true, onEvent }: UseSSEOptions) {
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed" | "error">("idle");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!url || !enabled) return;
    const ac = new AbortController();
    setStatus("connecting");
    streamSSE(url, {
      onOpen: () => setStatus("open"),
      onEvent: (e) => {
        onEventRef.current(e);
        if (e.kind === "done" || e.kind === "error") {
          setStatus("closed");
        }
      },
      onError: () => setStatus("error"),
      signal: ac.signal,
    }).catch(() => setStatus("error"));
    return () => {
      ac.abort();
      setStatus("closed");
    };
  }, [url, enabled]);

  return { status };
}
