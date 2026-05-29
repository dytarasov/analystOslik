import type { AgentEvent } from "@/lib/events";

export type StreamSSEOptions = {
  onEvent: (event: AgentEvent, rawId: string | null) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
  signal?: AbortSignal;
  lastEventId?: string | null;
  reconnectMs?: number[];
};

const DEFAULT_BACKOFF = [1000, 2000, 4000, 8000, 16000, 30000];

export async function streamSSE(url: string, opts: StreamSSEOptions): Promise<void> {
  const backoff = opts.reconnectMs ?? DEFAULT_BACKOFF;
  let attempt = 0;
  let lastId: string | null = opts.lastEventId ?? null;
  // True once a terminal event (done/error) has been dispatched, so we stop
  // instead of reconnecting. A clean EOF *without* a terminal event means the
  // connection dropped mid-run → reconnect with Last-Event-ID (backend replays).
  let sawTerminal = false;

  while (true) {
    if (opts.signal?.aborted) return;
    try {
      const headers: Record<string, string> = {
        Accept: "text/event-stream",
        "Cache-Control": "no-cache",
      };
      if (lastId) headers["Last-Event-ID"] = lastId;
      const res = await fetch(url, {
        credentials: "include",
        headers,
        signal: opts.signal,
      });
      if (!res.ok || !res.body) {
        const err: Error & { status?: number } = new Error(`SSE failed: ${res.status}`);
        err.status = res.status;
        throw err;
      }
      opts.onOpen?.();
      attempt = 0;
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      let eventName = "";
      let dataLines: string[] = [];
      let id: string | null = null;

      const flush = () => {
        if (!eventName && !dataLines.length) return;
        const dataStr = dataLines.join("\n");
        try {
          const payload = dataStr ? JSON.parse(dataStr) : {};
          const ev = { kind: eventName, ...payload } as unknown as AgentEvent;
          opts.onEvent(ev, id);
          if (id) lastId = id;
          if (ev.kind === "done" || ev.kind === "error") sawTerminal = true;
        } catch (err) {
          opts.onError?.(err);
        }
        eventName = "";
        dataLines = [];
        id = null;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += value;
        let nlIdx;
        while ((nlIdx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nlIdx).replace(/\r$/, "");
          buf = buf.slice(nlIdx + 1);
          if (line === "") {
            flush();
            continue;
          }
          if (line.startsWith(":")) continue;
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          const valueRaw = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
          if (field === "event") eventName = valueRaw;
          else if (field === "data") dataLines.push(valueRaw);
          else if (field === "id") id = valueRaw;
        }
      }
      // The read loop ended. If a terminal event arrived (or we were aborted),
      // we're done; otherwise the stream dropped mid-run — reconnect so the
      // timeline doesn't get stuck "running" forever after a clean EOF.
      if (sawTerminal || opts.signal?.aborted) return;
      const eofDelay = backoff[Math.min(attempt, backoff.length - 1)];
      attempt += 1;
      await new Promise((r) => setTimeout(r, eofDelay));
    } catch (err) {
      if (opts.signal?.aborted) return;
      const status = (err as { status?: number })?.status;
      // 4xx (e.g. 404 after a restart: the agent run no longer exists) is not
      // retryable — stop and let the caller fall back to polling task state.
      if (typeof status === "number" && status >= 400 && status < 500) {
        opts.onError?.(err);
        return;
      }
      opts.onError?.(err);
      const delay = backoff[Math.min(attempt, backoff.length - 1)];
      attempt += 1;
      await new Promise((r) => setTimeout(r, delay));
    }
  }
}
