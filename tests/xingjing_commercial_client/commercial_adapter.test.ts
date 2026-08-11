import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  commercialClientIntegrationGuide,
  commercialEtagFromVersion,
  createCommercialClient,
  createCommercialPageAdapter,
} from "../../frontend/src/xingjing_commercial_client";
import { startContractServer, type ContractServer } from "./contract_server";
import { commercialOrderFixture } from "./fixtures";

describe("M14 页面适配器导出", () => {
  let server: ContractServer;

  beforeEach(async () => {
    server = await startContractServer();
  });

  afterEach(async () => {
    await server.close();
  });

  it("通过判别联合命令把页面输入接到真实 FastAPI 端点", async () => {
    server.enqueueJson(200, commercialOrderFixture(8), { ETag: '"8"' });
    const adapter = createCommercialPageAdapter(createCommercialClient({
      baseUrl: `${server.origin}/api/v1`,
      accessToken: "access-token-1",
      workspaceId: "workspace-1",
    }));

    const result = await adapter.execute({
      type: "submit_delivery",
      orderId: "order-1",
      milestoneId: "milestone-1",
      input: {
        artifact_version_id: "video-v2",
        artifact_digest: "sha256:video-v2",
        note: "修订版",
      },
      command: {
        ifMatch: commercialEtagFromVersion(7),
        idempotencyKey: "delivery-v2",
      },
    });

    expect(result.version).toBe(8);
    expect(server.requests[0]).toMatchObject({
      method: "POST",
      url: "/api/v1/commercial-orders/order-1/milestones/milestone-1/deliveries",
    });
  });

  it("导出当前 M14 页面动作映射、必需输入与不可伪接的缺口", () => {
    expect(commercialClientIntegrationGuide).toMatchObject({
      importPath: "@/xingjing_commercial_client",
      endpointPrefix: "/api/v1/commercial-orders",
      requiredHeaders: ["Authorization", "X-Workspace-Id", "Idempotency-Key", "If-Match"],
      pageActions: {
        "CR-012": {
          acceptDelivery: "accept_delivery",
          rejectDelivery: "return_delivery",
        },
        "CR-013": {
          deliver: "submit_delivery",
        },
        "CR-014": {
          submitQuote: "submit_quote",
          signContract: "record_contract",
        },
        "CR-016": {
          openDispute: "open_dispute",
        },
      },
    });
    expect(commercialClientIntegrationGuide.unsupportedCurrentActions).toContainEqual({
      pageId: "CR-014",
      actionId: "updateMilestone",
      reason: "FastAPI 未提供里程碑更新端点",
    });
    expect(commercialClientIntegrationGuide.unsupportedCurrentActions).toContainEqual({
      pageId: "CR-016",
      actionId: "requestSettlement",
      reason: "结算由验收成功自动创建，FastAPI 未提供手工申请端点",
    });
  });
});
