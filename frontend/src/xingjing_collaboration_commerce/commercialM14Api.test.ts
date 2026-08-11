import { describe, expect, it, vi } from "vitest";

import { CommercialApiError } from "@/xingjing_commercial_client";

import { createCommercialM14PageApi } from "./commercialM14Api";
import { collaborationCommerceRouteMap } from "./routes";
import type { CommercialClient } from "@/xingjing_commercial_client";
import type { SessionContext } from "./types";

const session: SessionContext = {
  user: { id: "user-1" },
  activeWorkspace: { id: "workspace-1", name: "星镜内容团队" },
  workspaces: [{ id: "workspace-1", name: "星镜内容团队" }],
  permissions: ["commercial.view", "commercial.manage"],
  featureFlags: {},
};

const order = {
  id: "order-1", owner_workspace_id: "workspace-1", title: "第 3 集动画制作", requirements: "最终交付",
  budget_minor: 120000, currency: "CNY", milestones: [{ id: "milestone-1", title: "初稿", amount_minor: 120000, acceptance_criteria: "符合分镜", due_at: null, status: "pending", version: 2 }],
  status: "contracted", version: 7, created_at: "2026-07-16T08:00:00Z", updated_at: "2026-07-16T09:00:00Z",
  contractor_workspace_id: "workspace-2", accepted_quote_id: null, active_contract_version_id: null,
  quotes: [], contract_versions: [], deliveries: [], acceptance_records: [], settlements: [], disputes: [],
} as const;

function client(overrides: Partial<CommercialClient> = {}): CommercialClient {
  return {
    listOrders: vi.fn().mockResolvedValue({ items: [order], total: 1, offset: 0, limit: 25 }),
    getOrder: vi.fn().mockResolvedValue({ order, etag: '"7"', version: 7 }),
    listMilestones: vi.fn().mockResolvedValue({ items: order.milestones, total: 1 }),
    submitQuote: vi.fn().mockResolvedValue({ order, etag: '"8"', version: 8 }), acceptQuote: vi.fn(), recordContract: vi.fn(),
    submitDelivery: vi.fn(), returnDelivery: vi.fn(), acceptDelivery: vi.fn(), openDispute: vi.fn(), resolveDispute: vi.fn(),
    freezeSettlement: vi.fn(), resumeSettlement: vi.fn(), paySettlement: vi.fn(),
    ...overrides,
  };
}

describe("M14 CommercialClient 页面适配", () => {
  it("用 CommercialClient 列表读取并保留订单详情、版本和结算状态", async () => {
    const commercial = client();
    const api = createCommercialM14PageApi({ client: commercial, session });

    const snapshot = await api.loadPage(collaborationCommerceRouteMap.get("CR-015")!, { workspaceId: "workspace-1" });

    expect(commercial.listOrders).toHaveBeenCalledWith({ ownerWorkspaceId: "workspace-1", offset: 0, limit: 25 }, undefined);
    expect(snapshot.items[0]).toMatchObject({ id: "order-1", title: "第 3 集动画制作", version: 7, amountMinor: 120000, settlementStatus: "—" });
    expect(snapshot.detail).toMatchObject({ requirements: "最终交付", milestones: [expect.objectContaining({ id: "milestone-1" })] });
  });

  it("报价命令使用当前 ETag 与幂等键，且不伪造成功结果", async () => {
    const commercial = client();
    const api = createCommercialM14PageApi({ client: commercial, session, createId: () => "intent-1" });
    const route = collaborationCommerceRouteMap.get("CR-014")!;
    const action = route.actions.find((entry) => entry.id === "submitQuote")!;

    const result = await api.executeAction(route, action, { workspaceId: "workspace-1" }, {
      targetId: "order-1", version: 7,
      payload: { amount_minor: 120000, currency: "CNY", proposal: "按期完成", valid_until: "2026-08-01T00:00:00Z" },
    });

    expect(commercial.submitQuote).toHaveBeenCalledWith("order-1", expect.objectContaining({ amount_minor: 120000 }), {
      ifMatch: '"7"', idempotencyKey: "intent-1", requestId: undefined, signal: undefined,
    });
    expect(result).toMatchObject({ status: "succeeded", object: expect.objectContaining({ version: 7 }) });
  });

  it("将版本冲突原样交给页面，页面可读取新版本而不会盲目重试", async () => {
    const conflict = new CommercialApiError({ message: "版本已变化", kind: "conflict", status: 409, code: "VERSION_CONFLICT", requestId: "request-409", retryable: true, requiresRefresh: true, conflict: "version", details: null });
    const commercial = client({ submitQuote: vi.fn().mockRejectedValue(conflict) });
    const api = createCommercialM14PageApi({ client: commercial, session });
    const route = collaborationCommerceRouteMap.get("CR-014")!;
    const action = route.actions.find((entry) => entry.id === "submitQuote")!;

    await expect(api.executeAction(route, action, { workspaceId: "workspace-1" }, { targetId: "order-1", version: 7, payload: { amount_minor: 120000, currency: "CNY", proposal: "x", valid_until: "2026-08-01T00:00:00Z" } })).rejects.toBe(conflict);
  });
});
