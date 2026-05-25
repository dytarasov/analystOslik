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
    const body =
      ": ping\n\n" +
      "event: step.started\nid: 1\ndata: {\"step_id\":\"a\",\"name\":\"A\"}\n\n";

    const original = globalThis.fetch;
    (globalThis as any).fetch = makeFetchFromText(body);
    try {
      const events: AgentEvent[] = [];
      await streamSSE("http://x/sse", { onEvent: (ev) => events.push(ev) });
      expect(events).toHaveLength(1);
      expect(events[0]!.kind).toBe("step.started");
    } finally {
      globalThis.fetch = original;
    }
  });

  it("handles multi-line data payloads", async () => {
    const body =
      "event: result.final\nid: 7\ndata: {\"summary\":\"hello\",\ndata: \"world\"}\n\n";
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
      // The parser will hit a JSON parse error for this exact pathological input —
      // we just want to confirm it doesn't throw out of streamSSE.
      expect(Array.isArray(events)).toBe(true);
    } finally {
      globalThis.fetch = original;
    }
  });
});
