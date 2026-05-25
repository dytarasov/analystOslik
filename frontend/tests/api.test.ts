import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api, HttpError } from "@/lib/api";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  let original: typeof fetch;
  beforeEach(() => {
    original = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = original;
  });

  it("auth.login sends body and returns payload", async () => {
    const calls: Array<[RequestInfo | URL, RequestInit?]> = [];
    (globalThis as any).fetch = async (url: any, init: any) => {
      calls.push([url, init]);
      return jsonResponse(200, { login: "admin", expires_at: 1234 });
    };
    const res = await api.auth.login("admin", "p");
    expect(res).toEqual({ login: "admin", expires_at: 1234 });
    expect(String(calls[0]![0])).toContain("/api/admin/auth/login");
    const body = JSON.parse(String(calls[0]![1]!.body));
    expect(body).toEqual({ login: "admin", password: "p" });
  });

  it("non-2xx throws HttpError with parsed payload", async () => {
    (globalThis as any).fetch = async () =>
      jsonResponse(401, { code: "UNAUTHORIZED", message: "no" });
    await expect(api.auth.me()).rejects.toBeInstanceOf(HttpError);
  });

  it("204 returns undefined without parsing", async () => {
    (globalThis as any).fetch = async () =>
      new Response(null, { status: 204 });
    const res = await api.auth.logout();
    expect(res).toBeUndefined();
  });
});
