"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { AgentEvent } from "@/lib/events";
import { streamSSE } from "@/lib/sse";

// Shape of GET /api/tasks/{id} we rely on for the polling fallback (snake_case
// straight from the backend row).
type TaskRow = {
  status?: string;
  result_summary?: string | null;
  result_sql?: string | null;
  result_preview?: unknown;
  export_path?: string | null;
  error?: string | null;
};

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
  const [choices, setChoices] = useState<string[] | null>(null);
  const [result, setResult] = useState<TaskFinalResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Set once a done/error event arrives, so the post-stream handler knows the
  // run ended cleanly and skips the polling fallback.
  const sawTerminalRef = useRef(false);
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
    setChoices(null);
    setResult(null);
    setErrorMsg(null);
  }, []);

  const handleEvent = useCallback((event: AgentEvent) => {
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
      case "awaiting_input": {
        setState("awaiting_input");
        setQuestion(event.question);
        const sc = (event as { schema?: { choices?: string[] } }).schema?.choices;
        setChoices(Array.isArray(sc) && sc.length > 0 ? sc : null);
        break;
      }
      case "result.final":
        setResult({
          summary: event.summary,
          sql: event.sql,
          preview: event.preview,
          exportUrl: event.export_url,
        });
        break;
      case "error":
        sawTerminalRef.current = true;
        setState("error");
        setErrorMsg(event.message);
        break;
      case "done":
        sawTerminalRef.current = true;
        setState((s) => (s === "error" ? s : "done"));
        break;
      default:
        break;
    }
    onEventRef.current?.(event);
  }, []);

  // Fallback when the SSE stream ends without a terminal event (e.g. the agent
  // run was lost to a backend restart → 404 on reconnect). Pull the persisted
  // task state from Postgres a few times so a finished run still renders its
  // answer instead of spinning forever.
  const pollFinalState = useCallback(
    async (taskId: string, signal: AbortSignal) => {
      const delays = [800, 1500, 2500, 4000];
      for (const delay of delays) {
        if (signal.aborted) return;
        try {
          const t = (await api.client.getTask(taskId)) as TaskRow;
          if (t?.status === "done") {
            setErrorMsg(null);
            setResult({
              summary: t.result_summary ?? null,
              sql: t.result_sql ?? null,
              preview: t.result_preview ?? null,
              exportUrl: t.export_path ? api.client.exportUrl(taskId) : null,
            });
            setState("done");
            return;
          }
          if (t?.status === "failed") {
            setErrorMsg(t.error ?? "Не удалось сформировать ответ");
            setState("error");
            return;
          }
          if (t?.status === "cancelled") {
            setState("cancelled");
            return;
          }
          // still running/awaiting_input — wait and retry
        } catch {
          // getTask 404 / network — fall through to retry, then give up below
        }
        await new Promise((r) => setTimeout(r, delay));
      }
      if (!signal.aborted) {
        setState("error");
        setErrorMsg("Соединение потеряно. Обновите страницу.");
      }
    },
    [],
  );

  const start = useCallback(
    (url: string, startOpts?: { taskId?: string }) => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      sawTerminalRef.current = false;
      setState("connecting");
      setSteps([]);
      setTokens("");
      setQuestion(null);
      setChoices(null);
      setResult(null);
      setErrorMsg(null);
      streamSSE(url, {
        signal: ac.signal,
        onEvent: handleEvent,
        // Transient errors are handled by streamSSE's reconnect; don't flip the
        // UI to "error" on every blip — the post-stream handler below decides.
        onError: () => {},
      })
        .then(() => {
          if (ac.signal.aborted || sawTerminalRef.current) return;
          // Stream ended without done/error: try to recover the final state.
          if (startOpts?.taskId) void pollFinalState(startOpts.taskId, ac.signal);
          else {
            setState("error");
            setErrorMsg("Соединение потеряно. Обновите страницу.");
          }
        })
        .catch(() => {});
    },
    [handleEvent, pollFinalState],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState("cancelled");
  }, []);

  useEffect(() => () => abortRef.current?.abort(), []);

  return { state, steps, tokens, question, choices, result, errorMsg, start, cancel, reset };
}
