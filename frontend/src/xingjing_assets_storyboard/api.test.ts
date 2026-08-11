import { describe, expect, it, vi } from "vitest";

import { AssetsStoryboardApiError, createAssetsStoryboardApi } from "./api";

const scope = { workspaceId: "workspace-019f", projectId: "project-019f", episodeId: "episode-019f" };

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("资产与分镜 typed API", () => {
  it("资产列表携带工作区上下文、组合筛选与包含唯一键的稳定排序", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({
      data: [],
      meta: { requestId: "request-1", page: { nextToken: null }, collectionVersion: 7 },
    }));
    const api = createAssetsStoryboardApi({ fetcher, createId: () => "request-client-1" });

    await api.listAssets(scope, { assetType: "character", rightsStatus: "verified", search: "主角" });

    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/projects/project-019f/assets?");
    expect(url).toContain("assetType=character");
    expect(url).toContain("rightsStatus=verified");
    expect(url).toContain("search=%E4%B8%BB%E8%A7%92");
    expect(url).toContain("sort=updatedAt%3Adesc%2Cid%3Adesc");
    expect(new Headers(init.headers).get("X-Workspace-Id")).toBe("workspace-019f");
  });

  it("不确定网络失败后用同一幂等键安全重放，并发送带引号的版本", async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockResolvedValueOnce(jsonResponse({ data: { asset: { id: "asset-1", version: 4 } }, meta: { requestId: "request-2" } }));
    const ids = ["request-a", "idem-a", "request-b", "idem-b"];
    const api = createAssetsStoryboardApi({ fetcher, createId: () => ids.shift() ?? "fallback" });
    const input = { action: "updateAsset" as const, targetId: "asset-1", version: 3, payload: { name: "新名称" } };

    await expect(api.assetAction(scope, input)).rejects.toMatchObject({ code: "NETWORK_ERROR", retryable: true });
    await api.assetAction(scope, input);

    const firstHeaders = new Headers((fetcher.mock.calls[0]?.[1] as RequestInit).headers);
    const secondHeaders = new Headers((fetcher.mock.calls[1]?.[1] as RequestInit).headers);
    expect(secondHeaders.get("Idempotency-Key")).toBe(firstHeaders.get("Idempotency-Key"));
    expect(secondHeaders.get("If-Match")).toBe('"3"');
  });

  it("工作区资产动作不伪造项目上下文，发送到工作区作用域端点", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({ data: {}, meta: { requestId: "request-workspace" } }));
    const api = createAssetsStoryboardApi({ fetcher, createId: () => "request-workspace-client" });

    await api.assetAction({ workspaceId: "workspace-019f" }, { action: "licenseMarketAsset", targetId: "asset-market-1", version: 2, payload: {} });

    expect(fetcher.mock.calls[0]?.[0]).toContain("/api/v1/workspaces/workspace-019f/assets/actions");
  });

  it("解析统一错误信封并保留 409 的请求 ID 与当前版本摘要", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({
      error: {
        code: "VERSION_CONFLICT",
        message: "对象已被其他成员更新",
        retryable: false,
        details: [{ field: "version", reason: "expected 3, actual 4" }],
      },
      meta: { requestId: "request-conflict" },
    }, 409));
    const api = createAssetsStoryboardApi({ fetcher, createId: () => "request-3" });

    const caught = await api.shotAction(scope, {
      action: "updateShot",
      targetId: "shot-1",
      version: 3,
      payload: { durationMs: 2400 },
    }).catch((error: unknown) => error);

    expect(caught).toBeInstanceOf(AssetsStoryboardApiError);
    expect(caught).toMatchObject({
      status: 409,
      code: "VERSION_CONFLICT",
      requestId: "request-conflict",
      retryable: false,
      details: [{ field: "version", reason: "expected 3, actual 4" }],
    });
  });

  it("提交分镜表导入时从当前作用域补齐剧集上下文", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({ data: { shots: [] }, meta: { requestId: "request-import" } }));
    const api = createAssetsStoryboardApi({ fetcher, createId: () => "request-import-client" });

    await api.shotAction(scope, { action: "commitImport", payload: { uploadId: "upload-1", baseVersion: 3 } });

    const [, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toMatchObject({
      action: "commitImport",
      payload: { uploadId: "upload-1", baseVersion: 3, episodeId: "episode-019f" },
    });
  });
});
