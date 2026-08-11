import { describe, expect, it, vi } from "vitest";

import { createCollaborationCommerceApi } from "./api";
import { collaborationCommerceRouteMap } from "./routes";

const meta = { requestId: "9f1f7f7e-6ccd-4c29-8d39-05a09f32a77f", serverTime: "2026-07-16T08:00:00Z" };

describe("协作与商业 typed API", () => {
  it("读取页面时携带工作区/项目上下文并解析标准列表信封", async () => {
    const fetcher = vi.fn().mockImplementation(async () => new Response(JSON.stringify({
      data: [{ id: "member-1", name: "成员甲", status: "active", version: 3, updatedAt: "2026-07-16T07:00:00Z" }],
      meta: { ...meta, page: { size: 20, nextToken: "next-1" } },
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const api = createCollaborationCommerceApi({ fetcher, accessToken: "session-token", createId: () => "idempotency-key-0001" });
    const route = collaborationCommerceRouteMap.get("TM-010")!;

    const result = await api.loadPage(route, { workspaceId: "workspace-1", projectId: "project-8" }, {
      pageSize: 20,
      pageToken: "page-1",
      query: "视觉",
      status: "active",
      sort: "updatedAt:desc",
    });

    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("/api/v1/workspaces/workspace-1/members?"), expect.objectContaining({
      method: "GET",
      credentials: "include",
      headers: expect.objectContaining({
        Authorization: "Bearer session-token",
        "X-Workspace-Id": "workspace-1",
        "X-Project-Id": "project-8",
      }),
    }));
    const url = String(fetcher.mock.calls[0]?.[0]);
    expect(url).toContain("pageSize=20");
    expect(url).toContain("pageToken=page-1");
    expect(url).toContain("query=%E8%A7%86%E8%A7%89");
    expect(result).toMatchObject({ requestId: meta.requestId, items: [{ id: "member-1" }], nextPageToken: "next-1" });
  });

  it("将服务端 403 错误信封映射为不泄露响应细节的权限拒绝", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: "FORBIDDEN", message: "对象 剧本A 不可访问", retryable: false, details: [] },
      meta,
    }), { status: 403, headers: { "Content-Type": "application/json" } }));
    const api = createCollaborationCommerceApi({ fetcher });

    await expect(api.getSessionContext()).rejects.toMatchObject({
      name: "CollaborationCommerceApiError",
      status: 403,
      code: "FORBIDDEN",
      kind: "forbidden",
      requestId: meta.requestId,
      retryable: false,
    });
  });

  it("网络恢复重试复用同一幂等键，并携带带引号的对象版本", async () => {
    const fetcher = vi.fn()
      .mockRejectedValueOnce(new TypeError("network"))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        data: { requestId: meta.requestId, status: "succeeded", object: { id: "order-1", version: 8 } },
        meta,
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const api = createCollaborationCommerceApi({ fetcher, createId: () => "stable-idempotency-0001" });
    const route = collaborationCommerceRouteMap.get("CR-013")!;
    const action = route.actions.find((candidate) => candidate.id === "deliver")!;

    await api.executeAction(route, action, { workspaceId: "workspace-1", projectId: "project-8" }, {
      targetId: "order-1",
      version: 7,
      payload: { deliveryNote: "已上传最终交付包" },
    }, { retryNetworkOnce: true });

    expect(fetcher).toHaveBeenCalledTimes(2);
    const first = fetcher.mock.calls[0]?.[1] as RequestInit;
    const second = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(first.headers).get("Idempotency-Key")).toBe("stable-idempotency-0001");
    expect(new Headers(second.headers).get("Idempotency-Key")).toBe("stable-idempotency-0001");
    expect(new Headers(first.headers).get("If-Match")).toBe('"7"');
    expect(first.body).toContain('"action":"deliver"');
  });

  it("阻止客户上下文调用未列入页面规格的生产动作", async () => {
    const fetcher = vi.fn();
    const api = createCollaborationCommerceApi({ fetcher });
    const route = collaborationCommerceRouteMap.get("CL-002")!;
    const forgedAction = { ...route.actions[0]!, id: "editProduction" };

    await expect(api.executeAction(route, forgedAction, { reviewToken: "review-token-abcdefghijklmnopqrstuvwxyz" }, {
      payload: {},
    })).rejects.toMatchObject({ code: "CLIENT_ACTION_FORBIDDEN" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("客户通过验收后使用服务端新版本确认交付", async () => {
    const response = (data: unknown) => new Response(JSON.stringify({ data, meta }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({
        session: { id: "session-1", ticket: "ticket-1" },
        review: { delivery: { version: 4, status: "pending" } },
      }))
      .mockResolvedValueOnce(response({ delivery: { version: 5, status: "approved" } }))
      .mockResolvedValueOnce(response({ delivery: { version: 6, status: "confirmed" } }));
    const api = createCollaborationCommerceApi({ fetcher, createId: () => "review-action-key" });
    const route = collaborationCommerceRouteMap.get("CL-001")!;
    const context = { reviewToken: "review-token-abcdefghijklmnopqrstuvwxyz" };

    await api.loadPage(route, context);
    await api.executeAction(route, route.actions.find((action) => action.id === "approve")!, context, { payload: {} });
    await api.executeAction(route, route.actions.find((action) => action.id === "confirmDelivery")!, context, { payload: {} });

    expect(JSON.parse(String((fetcher.mock.calls[2]?.[1] as RequestInit).body))).toEqual({
      action: "confirm_delivery",
      version: 5,
    });
  });
});
