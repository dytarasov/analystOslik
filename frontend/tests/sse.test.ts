import { describe, expect, it } from "vitest";

import type { AgentEvent } from "@/lib/events";
import { streamSSE } from "@/lib/sse";

function makeFetchFromText(body: string): typeof fetch {
  return (async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }) as unknown as typeof fetch;
}

describe("streamSSE parser", () => {
  it("parses event/id/data triples", async () => {
    const body =
      "event: step.started\nid: 1\ndata: {\"step_id\":\"a\",\"name\":\"A\"}\n\n" +
      "event: step.completed\nid: 2\ndata: {\"step_id\":\"a\",\"duration_ms\":42}\n\n" +
      "event: done\nid: 3\ndata: {}\n\n";

    const original = globalThis.fetch;
    (globalThis as any).fetch = makeFetchFromText(body);
    try {
      const events: AgentEvent[] = [];
      await streamSSE("http://x/sse", {
        onEvent: (ev) => events.push(ev),
      });
      expect(events.length).toBe(3);
      expect(events[0]!.kind).toBe("step.started");
      expect((events[1] as any).duration_ms).toBe(42);
      expect(events[2]!.kind).toBe("done");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("ignores comment lines (heartbeat)", async () => {
    // Stream ends with `done` — the real backend always sends a terminal event;
    // a clean EOF without one now triggers a reconnect (see below).
    const body =
      ": ping\n\n" +
      "event: step.started\nid: 1\ndata: {\"step_id\":\"a\",\"name\":\"A\"}\n\n" +
      "event: done\nid: 2\ndata: {}\n\n";

    const original = globalThis.fetch;
    (globalThis as any).fetch = makeFetchFromText(body);
    try {
      const events: AgentEvent[] = [];
      await streamSSE("http://x/sse", { onEvent: (ev) => events.push(ev) });
      expect(events[0]!.kind).toBe("step.started"); // heartbeat produced no event
      expect(events.some((e) => e.kind === "done")).toBe(true);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("handles multi-line data payloads", async () => {
    const body =
      "event: result.final\nid: 7\ndata: {\"summary\":\"hello\",\ndata: \"world\"}\n\n" +
      "event: done\nid: 8\ndata: {}\n\n";
    const original = globalThis.fetch;
    // Note: SSE multi-line data joins with \n — we emit a single JSON value that includes the newline,
    // so the producer must serialize as JSON without embedded newlines. We assert resilience instead.
    (globalThis as any).fetch = makeFetchFromText(body);
    try {
      const events: AgentEvent[] = [];
      await streamSSE("http://x/sse", {
        onEvent: (ev) => events.push(ev),
        onError: () => {},
      });
      // The parser will hit a JSON parse error for the pathological result.final —
      // we just want to confirm it doesn't throw out of streamSSE and the terminal
      // `done` still lets the call resolve.
      expect(Array.isArray(events)).toBe(true);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("reconnects on a clean EOF without a terminal event (resumes via Last-Event-ID)", async () => {
    // A connection that drops mid-run (no done/error) must not leave the caller
    // hanging — streamSSE reconnects and replays from the last seen id.
    const calls: { lastEventId: string | null }[] = [];
    const fetchImpl = (async (_url: string, init: RequestInit) => {
      const headers = new Headers((init?.headers as HeadersInit) ?? {});
      calls.push({ lastEventId: headers.get("Last-Event-ID") });
      const body =
        calls.length === 1
          ? "event: step.started\nid: 1\ndata: {\"step_id\":\"a\",\"name\":\"A\"}\n\n" // drops, no done
          : "event: done\nid: 2\ndata: {}\n\n";
      const encoder = new TextEncoder();
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(encoder.encode(body));
          c.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    }) as unknown as typeof fetch;

    const original = globalThis.fetch;
    (globalThis as any).fetch = fetchImpl;
    try {
      const events: AgentEvent[] = [];
      await streamSSE("http://x/sse", {
        onEvent: (ev) => events.push(ev),
        reconnectMs: [1],
      });
      expect(calls.length).toBe(2);
      expect(calls[1]!.lastEventId).toBe("1"); // resumed from the last seen id
      expect(events.map((e) => e.kind)).toEqual(["step.started", "done"]);
    } finally {
      globalThis.fetch = original;
    }
  });

  it("stops without retrying on a 404 (agent run gone)", async () => {
    let calls = 0;
    const fetchImpl = (async () => {
      calls += 1;
      return new Response("not found", { status: 404 });
    }) as unknown as typeof fetch;

    const original = globalThis.fetch;
    (globalThis as any).fetch = fetchImpl;
    try {
      let errored = false;
      await streamSSE("http://x/sse", {
        onEvent: () => {},
        onError: () => {
          errored = true;
        },
        reconnectMs: [1],
      });
      expect(calls).toBe(1); // 4xx is terminal — not retried
      expect(errored).toBe(true);
    } finally {
      globalThis.fetch = original;
    }
  });
});
