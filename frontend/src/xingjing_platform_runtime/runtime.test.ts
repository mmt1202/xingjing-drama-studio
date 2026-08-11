import { describe, expect, it, vi } from "vitest";

import {
  ApiError,
  createApiClient,
  createMemorySessionPort,
  createPermissionGate,
  createSseClient,
  createWorkspaceContext,
  normalizeNotification,
  normalizeTask,
} from "./index";

describe("星镜共享运行时", () => {
  it("会话凭证仅通过安全端口驻留并可清除", async () => {
    const session = createMemorySessionPort();
    await session.set({ accessToken: "short-lived", expiresAt: 2_000 });
    expect(await session.get()).toEqual({ accessToken: "short-lived", expiresAt: 2_000 });
    await session.clear();
    expect(await session.get()).toBeNull();
  });

  it("API 请求携带工作区、request-id 和写请求幂等键", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    const session = createMemorySessionPort({ accessToken: "token", expiresAt: Date.now() + 10_000 });
    const workspace = createWorkspaceContext({ id: "ws-1", version: 3 });
    const client = createApiClient({ fetcher, session, workspace, createId: () => "req-1" });

    await client.request("/generation-tasks", { method: "POST", json: { prompt: "x" } });

    const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer token");
    expect(headers.get("X-Workspace-ID")).toBe("ws-1");
    expect(headers.get("X-Request-ID")).toBe("req-1");
    expect(headers.get("Idempotency-Key")).toBe("req-1");
  });

  it.each([
    [401, "unauthenticated", false],
    [403, "forbidden", false],
    [409, "conflict", true],
    [429, "rate_limited", true],
  ] as const)("统一映射 HTTP %s", async (status, kind, retryable) => {
    const session = createMemorySessionPort({ accessToken: "token", expiresAt: Date.now() + 10_000 });
    const client = createApiClient({
      fetcher: async () => new Response(JSON.stringify({ code: "E", message: "失败" }), {
        status,
        headers: { "content-type": "application/json", "retry-after": "2", "x-request-id": "srv-1" },
      }),
      session,
      workspace: createWorkspaceContext({ id: "ws-1", version: 1 }),
      createId: () => "req-1",
    });

    const error = await client.request("/x").catch((value: unknown) => value);
    expect(error).toMatchObject({ kind, status, retryable, requestId: "srv-1" });
    expect(error).toBeInstanceOf(ApiError);
    if (status === 401) expect(await session.get()).toBeNull();
  });

  it("调用方取消请求时保留 AbortError 语义", async () => {
    const controller = new AbortController();
    const client = createApiClient({
      fetcher: async (_input, init) => new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      }),
      session: createMemorySessionPort(),
      workspace: createWorkspaceContext({ id: "ws-1", version: 1 }),
    });
    const pending = client.request("/slow", { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("SSE 续传游标、去重事件并在断线后退避重连", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode("id: 7\nevent: task\ndata: {\"status\":\"running\"}\n\n"));
          controller.close();
        },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode("id: 7\nevent: task\ndata: {\"status\":\"running\"}\n\nid: 8\ndata: {\"ok\":true}\n\n"));
          controller.close();
        },
      }), { status: 200 }));
    const events: unknown[] = [];
    const stream = createSseClient({ fetcher, minRetryMs: 10, maxRetryMs: 20, random: () => 0 });
    const connection = stream.connect("/events", { onEvent: (event) => events.push(event) });
    await vi.advanceTimersByTimeAsync(30);
    expect(new Headers((fetcher.mock.calls[1]?.[1] as RequestInit).headers).get("Last-Event-ID")).toBe("7");
    expect(events).toHaveLength(2);
    connection.close();
    vi.useRealTimers();
  });

  it("任务和通知状态按平台契约归一化", () => {
    expect(normalizeTask({ id: "t1", status: "timeout", progress: 150 })).toMatchObject({
      id: "t1", phase: "failed", terminal: true, progress: 100, canRetry: true,
    });
    expect(normalizeNotification({ id: "n1", read_at: null, category: "task", created_at: "2026-07-15T00:00:00Z" })).toMatchObject({
      id: "n1", read: false, category: "task",
    });
  });

  it("权限门禁支持全部/任一权限且默认拒绝", () => {
    const gate = createPermissionGate(["generation.view", "generation.manage"]);
    expect(gate.all("generation.view", "generation.manage")).toBe(true);
    expect(gate.any("workspace.manage", "generation.view")).toBe(true);
    expect(gate.all("workspace.manage")).toBe(false);
  });
});
