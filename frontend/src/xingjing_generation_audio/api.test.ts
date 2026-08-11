import { afterEach, describe, expect, it, vi } from "vitest";
import { createGenerationAudioApi, generationAudioApi } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("typed API 端口", () => {
  it("每个请求都携带所选工作区", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }), { status: 200 }));
    const api = createGenerationAudioApi({ workspaceId: "workspace-7", fetcher });
    await api.tasks("project-9");
    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-Workspace-Id")).toBe("workspace-7");
  });
  it("生成写操作携带幂等键和版本", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "t1", status: "cancelling" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await generationAudioApi.actionTask({ id: "t1", kind: "video", model_id: "m", status: "running", version: 7 }, "cancel", {}, "key-1");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.get("Idempotency-Key")).toBe("key-1");
    expect(init.headers.get("If-Match")).toBe("7");
  });

  it("将 409 解析为可恢复的版本冲突", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "VERSION_CONFLICT", message: "已被他人更新" }), { status: 409, headers: { "Content-Type": "application/json" } })));
    await expect(generationAudioApi.audioAction("p1", {}, "k", 2)).rejects.toMatchObject({ detail: { status: 409, code: "VERSION_CONFLICT" } });
  });
});
