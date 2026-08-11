import { describe, expect, it, vi } from "vitest";
import { createCreatorProjectsApi, CreatorApiError } from "./api";

describe("星镜项目 typed API", () => {
  it("写操作携带工作区、幂等键与乐观锁版本", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { id: "p1", version: 8 }, meta: { requestId: "r1" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const api = createCreatorProjectsApi({ workspaceId: "w1", fetcher, createId: () => "idem-1" });

    await api.projectAction("p1", "update", { name: "新名称" }, 7);

    expect(fetcher).toHaveBeenCalledWith("/api/v1/projects/p1/actions", expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-Workspace-Id": "w1", "Idempotency-Key": "idem-1", "If-Match": '"7"' }),
    }));
  });

  it("将 409 解析为保留请求 ID 的类型化冲突", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "VERSION_CONFLICT", message: "对象已更新", retryable: false }, meta: { requestId: "req-conflict" } }), { status: 409, headers: { "Content-Type": "application/json" } }));
    const api = createCreatorProjectsApi({ workspaceId: "w1", fetcher });

    await expect(api.getProject("p1")).rejects.toMatchObject({ status: 409, code: "VERSION_CONFLICT", requestId: "req-conflict" } satisfies Partial<CreatorApiError>);
  });

  it("失败后的相同写入重试复用幂等键，成功后才释放", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "SERVICE_UNAVAILABLE", message: "暂不可用", retryable: true } }), { status: 503, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: "p1", version: 2 } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const api = createCreatorProjectsApi({ workspaceId: "w1", fetcher, createId: vi.fn().mockReturnValueOnce("stable-key").mockReturnValueOnce("new-key") });
    await expect(api.projectAction("p1", "archive", {}, 1)).rejects.toBeInstanceOf(CreatorApiError);
    await api.projectAction("p1", "archive", {}, 1);
    expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe("stable-key");
    expect(fetcher.mock.calls[1][1].headers["Idempotency-Key"]).toBe("stable-key");
  });
});
