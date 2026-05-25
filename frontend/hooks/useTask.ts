"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AgentEvent } from "@/lib/events";
import { streamSSE } from "@/lib/sse";

export type StepInfo = {
  id: string;
  name: string;
  status: "running" | "completed" | "failed";
  progress?: number;
  detail?: string | null;
  durationMs?: number;
  error?: string;
};

export type TaskFinalResult = {
  summary: string | null;
  sql: string | null;
  preview: unknown;
  exportUrl: string | null;
};

export type TaskFsmState =
  | "idle"
  | "connecting"
  | "running"
  | "awaiting_input"
  | "done"
  | "error"
  | "cancelled";

export type UseTaskOptions = {
  onEvent?: (event: AgentEvent) => void;
};

export function useTask(opts: UseTaskOptions = {}) {
  const [state, setState] = useState<TaskFsmState>("idle");
  const [steps, setSteps] = useState<StepInfo[]>([]);
  const [tokens, setTokens] = useState<string>("");
  const [question, setQuestion] = useState<string | null>(null);
  const [result, setResult] = useState<TaskFinalResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts.onEvent);
  onEventRef.current = opts.onEvent;

  const reset = useCallback(() => {
    // Также рвём текущий SSE-стрим — иначе при смене сессии события старого
    // чата продолжают капать в state.
    abortRef.current?.abort();
    abortRef.current = null;
    setState("idle");
    setSteps([]);
    setTokens("");
    setQuestion(null);
    setResult(null);
    setErrorMsg(null);
  }, []);

  const handleEvent = useCallback((event: AgentEvent) => {
    // Verbose by design — easy to find in browser devtools when debugging cancel/SSE.
    // eslint-disable-next-line no-console
    console.debug("[useTask] event", event.kind, event);
    switch (event.kind) {
      case "step.started":
        setState("running");
        setSteps((prev) => [
          ...prev,
          { id: event.step_id, name: event.name, status: "running" },
        ]);
        break;
      case "step.progress":
        setSteps((prev) =>
          prev.map((s) =>
            s.id === event.step_id
              ? { ...s, progress: event.progress, detail: event.detail }
              : s,
          ),
        );
        break;
      case "step.completed":
        setSteps((prev) =>
          prev.map((s) =>
            s.id === event.step_id
              ? { ...s, status: "completed", durationMs: event.duration_ms }
              : s,
          ),
        );
        break;
      case "step.failed":
        setSteps((prev) =>
          prev.map((s) =>
            s.id === event.step_id
              ? { ...s, status: "failed", error: event.error }
              : s,
          ),
        );
        break;
      case "llm.token":
        setTokens((t) => t + event.chunk);
        break;
      case "awaiting_input":
        setState("awaiting_input");
        setQuestion(event.question);
        break;
      case "result.final":
        setResult({
          summary: event.summary,
          sql: event.sql,
          preview: event.preview,
          exportUrl: event.export_url,
        });
        break;
      case "error":
        setState("error");
        setErrorMsg(event.message);
        break;
      case "done":
        setState((s) => (s === "error" ? s : "done"));
        break;
      default:
        break;
    }
    onEventRef.current?.(event);
  }, []);

  const start = useCallback(
    (url: string) => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setState("connecting");
      setSteps([]);
      setTokens("");
      setQuestion(null);
      setResult(null);
      setErrorMsg(null);
      streamSSE(url, {
        signal: ac.signal,
        onEvent: handleEvent,
        onError: (err) => {
          if (!ac.signal.aborted) {
            setState("error");
            setErrorMsg(String(err));
          }
        },
      }).catch(() => {});
    },
    [handleEvent],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState("cancelled");
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { state, steps, tokens, question, result, errorMsg, start, cancel, reset };
}
