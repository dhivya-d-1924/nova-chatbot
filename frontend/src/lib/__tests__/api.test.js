import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, parseSSEBlock, session, streamSSE } from "../api";

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function sseResponse(chunks) {
  const enc = new TextEncoder();
  return new Response(new ReadableStream({
    start(c) { chunks.forEach((x) => c.enqueue(enc.encode(x))); c.close(); },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("api client", () => {
  beforeEach(() => { session.clear(); });

  it("sends cookies and the CSRF token only on state-changing requests", async () => {
    session.setCsrf("tok123");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json([]));
    await api.listConversations();
    await api.createConversation();
    const [getCall, postCall] = fetchMock.mock.calls;
    expect(getCall[1].credentials).toBe("include");
    expect(getCall[1].headers["X-CSRF-Token"]).toBeUndefined();
    expect(postCall[1].method).toBe("POST");
    expect(postCall[1].headers["X-CSRF-Token"]).toBe("tok123");
  });

  it("never stores tokens in localStorage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ user: { id: "1" }, csrf: "abc" }));
    await api.login("a@b.co", "x");
    expect(setItem).not.toHaveBeenCalled();
  });

  it("retries GET requests after a network error with backoff", async () => {
    let n = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      n += 1;
      if (n < 2) throw new TypeError("network down");
      return json([{ id: "c1" }]);
    });
    const list = await api.listConversations();
    expect(n).toBe(2);
    expect(list[0].id).toBe("c1");
  });

  it("does not retry POST requests (no duplicate side effects)", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("down"));
    await expect(api.createConversation()).rejects.toMatchObject({ network: true });
    expect(f).toHaveBeenCalledTimes(1);
  });

  it("surfaces server error messages and codes", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ error: "Too many requests.", code: "rate_limited" }, 429));
    await expect(api.createConversation()).rejects.toMatchObject({ message: "Too many requests.", status: 429, code: "rate_limited" });
  });

  it("refreshes an outdated CSRF token once and retries", async () => {
    session.setCsrf("old");
    const calls = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url, opts) => {
      calls.push([url, opts?.headers?.["X-CSRF-Token"]]);
      if (url.endsWith("/auth/me")) return json({ user: {}, csrf: "new" });
      if (opts.headers["X-CSRF-Token"] === "old") return json({ error: "csrf", code: "csrf" }, 403);
      return json({ id: "c2" }, 201);
    });
    const r = await api.createConversation();
    expect(r.id).toBe("c2");
    expect(calls.at(-1)[1]).toBe("new");
  });

  it("announces an expired session", async () => {
    session.setCsrf("t");
    const handler = vi.fn();
    window.addEventListener("auth:expired", handler);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ error: "Please sign in.", code: "unauthenticated" }, 401));
    await expect(api.listDocuments()).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalled();
    window.removeEventListener("auth:expired", handler);
  });
});

describe("server-sent events", () => {
  it("ignores heartbeat comments and parses data lines", () => {
    expect(parseSSEBlock(": ping")).toBeNull();
    expect(parseSSEBlock('data: {"type":"delta","text":"hi"}')).toEqual({ type: "delta", text: "hi" });
  });

  it("streams deltas split across chunks and finishes on done", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse([
      'data: {"type":"delta","text":"Hel"}\n\n: ping\n\ndata: {"type":"del', 'ta","text":"lo"}\n\n',
      'data: {"type":"done","message":{"id":"m1","content":"Hello"}}\n\n',
    ]));
    const deltas = [];
    const onDone = vi.fn();
    await streamSSE("/chat/stream", {}, { onDelta: (t) => deltas.push(t), onDone });
    expect(deltas.join("")).toBe("Hello");
    expect(onDone).toHaveBeenCalledWith({ id: "m1", content: "Hello" });
  });

  it("reports a dropped connection when the stream ends without done", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(['data: {"type":"delta","text":"partial"}\n\n']));
    await expect(streamSSE("/chat/stream", {}, { onDelta: () => {}, onDone: () => {} })).rejects.toMatchObject({ dropped: true });
  });

  it("passes along a partial reply saved by the server on error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse([
      'data: {"type":"error","message":"Provider failed","saved":{"id":"m9","content":"part"}}\n\n']));
    await expect(streamSSE("/chat/stream", {}, { onDelta: () => {}, onDone: () => {} }))
      .rejects.toMatchObject({ message: "Provider failed", saved: { id: "m9" } });
  });

  it("marks errors before streaming as preStream (nothing stored)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ error: "No AI provider", code: "llm_not_configured" }, 503));
    await expect(streamSSE("/chat/stream", {}, { onDelta: () => {}, onDone: () => {} }))
      .rejects.toMatchObject({ preStream: true, code: "llm_not_configured" });
  });
});
