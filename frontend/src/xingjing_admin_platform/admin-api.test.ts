import { describe, expect, it, vi } from "vitest";

import { createAdminApiClient } from "./admin-api";

describe("后台 typed API 客户端", () => {
  it("每次请求携带独立后台会话、工作区和项目上下文", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], page: 1, pageSize: 20, total: 0 }), { status: 200 }));
    const api = createAdminApiClient({
      sessionToken: "admin-session",
      workspaceId: "ws-1",
      projectId: "project-8",
      fetcher,
    });

    await api.list("business", { page: 1, pageSize: 20 });

    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/api/v1/admin/business-objects"), expect.objectContaining({
      credentials: "include",
      headers: expect.objectContaining({
        Authorization: "Bearer admin-session",
        "X-Workspace-Id": "ws-1",
        "X-Project-Id": "project-8",
      }),
    }));
  });

  it("幂等动作在网络恢复重试时复用同一 Idempotency-Key 并携带版本", async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ requestId: "req-1", status: "succeeded" }), { status: 200 }));
    const api = createAdminApiClient({ sessionToken: "admin-session", workspaceId: "ws-1", fetcher });

    await api.act("finance", { action: "approve", objectId: "invoice-1", version: 4 }, { retryNetworkOnce: true });

    const first = fetcher.mock.calls[0]?.[1] as RequestInit;
    const second = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect((first.headers as Record<string, string>)["Idempotency-Key"]).toBe((second.headers as Record<string, string>)["Idempotency-Key"]);
    expect(first.body).toContain('"version":4');
  });

  it("将 409 映射为可恢复版本冲突", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "VERSION_CONFLICT", requestId: "req-c" }), { status: 409 }));
    const api = createAdminApiClient({ sessionToken: "admin-session", workspaceId: "ws-1", fetcher });

    await expect(api.act("operations", { action: "publish", objectId: "notice-1", version: 2 })).rejects.toMatchObject({
      status: 409,
      code: "VERSION_CONFLICT",
      recoverable: true,
    });
  });
});
