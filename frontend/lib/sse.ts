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
        throw new Error(`SSE failed: ${res.status}`);
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
      return;
    } catch (err) {
      if (opts.signal?.aborted) return;
      opts.onError?.(err);
      const delay = backoff[Math.min(attempt, backoff.length - 1)];
      attempt += 1;
      await new Promise((r) => setTimeout(r, delay));
    }
  }
}
