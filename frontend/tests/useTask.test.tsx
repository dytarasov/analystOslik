import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useTask } from "@/hooks/useTask";

function mockFetchEvents(events: string) {
  return (async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(events));
        controller.close();
      },
    });
    return new Response(stream, { status: 200 });
  }) as unknown as typeof fetch;
}

describe("useTask FSM", () => {
  let originalFetch: typeof fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("transitions idle -> running -> done and collects steps", async () => {
    (globalThis as any).fetch = mockFetchEvents(
      "event: step.started\nid: 1\ndata: {\"step_id\":\"s1\",\"name\":\"Step 1\"}\n\n" +
        "event: step.completed\nid: 2\ndata: {\"step_id\":\"s1\",\"duration_ms\":15}\n\n" +
        "event: done\nid: 3\ndata: {}\n\n",
    );

    const { result } = renderHook(() => useTask());
    expect(result.current.state).toBe("idle");
    act(() => {
      result.current.start("http://x/sse");
    });
    await waitFor(() => expect(result.current.state).toBe("done"));
    expect(result.current.steps).toHaveLength(1);
    expect(result.current.steps[0]!.status).toBe("completed");
    expect(result.current.steps[0]!.durationMs).toBe(15);
  });

  it("captures awaiting_input then ends in done", async () => {
    (globalThis as any).fetch = mockFetchEvents(
      "event: step.started\nid: 1\ndata: {\"step_id\":\"s1\",\"name\":\"S\"}\n\n" +
        "event: awaiting_input\nid: 2\ndata: {\"question\":\"уточните?\"}\n\n" +
        "event: done\nid: 3\ndata: {}\n\n",
    );
    const { result } = renderHook(() => useTask());
    act(() => {
      result.current.start("http://x/sse");
    });
    await waitFor(() => expect(result.current.state).toBe("done"));
    expect(result.current.question).toBe("уточните?");
  });

  it("error event flips state to error", async () => {
    (globalThis as any).fetch = mockFetchEvents(
      "event: error\nid: 1\ndata: {\"code\":\"UPSTREAM\",\"message\":\"oops\"}\n\n",
    );
    const { result } = renderHook(() => useTask());
    act(() => {
      result.current.start("http://x/sse");
    });
    await waitFor(() => expect(result.current.state).toBe("error"));
    expect(result.current.errorMsg).toBe("oops");
  });

  it("custom onEvent receives every event", async () => {
    (globalThis as any).fetch = mockFetchEvents(
      "event: profiling.table.started\nid: 1\ndata: {\"database\":\"d\",\"table\":\"t\",\"idx\":1,\"total\":1}\n\n" +
        "event: done\nid: 2\ndata: {}\n\n",
    );
    const seen: string[] = [];
    const { result } = renderHook(() =>
      useTask({ onEvent: (e) => seen.push(e.kind) }),
    );
    act(() => {
      result.current.start("http://x/sse");
    });
    await waitFor(() => expect(seen).toContain("done"));
    expect(seen).toContain("profiling.table.started");
  });
});
