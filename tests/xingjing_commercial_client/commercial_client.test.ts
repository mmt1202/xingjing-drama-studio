import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CommercialApiError,
  commercialEtagFromVersion,
  createCommercialClient,
} from "../../frontend/src/xingjing_commercial_client";
import { startContractServer, type ContractServer } from "./contract_server";
import { commercialOrderFixture } from "./fixtures";

describe("M14 商单 FastAPI 强类型客户端", () => {
  let server: ContractServer;

  beforeEach(async () => {
    server = await startContractServer();
  });

  afterEach(async () => {
    await server.close();
  });

  function createClient() {
    return createCommercialClient({
      baseUrl: `${server.origin}/api/v1`,
      accessToken: "access-token-1",
      workspaceId: "workspace-1",
    });
  }

  it("使用真实 HTTP 请求读取商单列表、详情和 ETag", async () => {
    const order = commercialOrderFixture();
    server.enqueueJson(200, { items: [order], total: 1, offset: 20, limit: 10 });
    server.enqueueJson(200, order, { ETag: '"7"' });
    const client = createClient();

    const page = await client.listOrders({
      ownerWorkspaceId: "workspace-owner",
      offset: 20,
      limit: 10,
    });
    const detail = await client.getOrder("order-1");

    expect(page).toEqual({ items: [order], total: 1, offset: 20, limit: 10 });
    expect(detail).toEqual({ order, etag: '"7"', version: 7 });

    const listRequest = server.requests[0]!;
    const listUrl = new URL(listRequest.url, server.origin);
    expect(listRequest.method).toBe("GET");
    expect(listUrl.pathname).toBe("/api/v1/commercial-orders");
    expect(Object.fromEntries(listUrl.searchParams)).toEqual({
      owner_workspace_id: "workspace-owner",
      offset: "20",
      limit: "10",
    });
    expect(listRequest.headers.authorization).toBe("Bearer access-token-1");
    expect(listRequest.headers["x-workspace-id"]).toBe("workspace-1");

    expect(server.requests[1]).toMatchObject({
      method: "GET",
      url: "/api/v1/commercial-orders/order-1",
    });
  });

  it("按 FastAPI schemas 发送报价、合同、交付、打回、验收、争议与结算请求", async () => {
    const updatedOrder = commercialOrderFixture(8);
    for (let index = 0; index < 11; index += 1) {
      server.enqueueJson(200, updatedOrder, { ETag: '"8"' });
    }
    const client = createClient();
    const command = {
      ifMatch: commercialEtagFromVersion(7),
      idempotencyKey: "idempotency-key-1",
      requestId: "request-1",
    };

    await client.submitQuote("order-1", {
      amount_minor: 9_000,
      currency: "CNY",
      proposal: "七日内交付",
      valid_until: "2026-07-18T08:00:00+00:00",
    }, command);
    await client.acceptQuote("order-1", "quote-1", command);
    await client.recordContract("order-1", {
      content_ref: "object://contracts/v1.pdf",
      content_digest: "sha256:contract",
      amount_minor: 9_000,
    }, command);
    await client.submitDelivery("order-1", "milestone-1", {
      artifact_version_id: "video-v1",
      artifact_digest: "sha256:video-v1",
      note: "首版",
    }, command);
    await client.returnDelivery("order-1", "delivery-1", {
      reason: "片尾标识不符合合同",
    }, command);
    await client.acceptDelivery("order-1", "delivery-2", {
      evidence_ref: "object://acceptance/signature.json",
    }, command);
    await client.openDispute("order-1", "milestone-1", {
      kind: "acceptance",
      reason: "签收证据主体不一致",
    }, command);
    await client.resolveDispute("order-1", "dispute-1", {
      resolution: "证据已补正",
    }, command);
    await client.freezeSettlement("order-1", "settlement-1", {
      amount_minor: 10_000,
      currency: "CNY",
    }, command);
    await client.resumeSettlement("order-1", "settlement-1", {
      amount_minor: 10_000,
      currency: "CNY",
    }, command);
    await client.paySettlement("order-1", "settlement-1", {
      amount_minor: 10_000,
      currency: "CNY",
    }, command);

    const expected = [
      ["POST", "/api/v1/commercial-orders/order-1/quotes", {
        amount_minor: 9_000,
        currency: "CNY",
        proposal: "七日内交付",
        valid_until: "2026-07-18T08:00:00+00:00",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/quotes/quote-1/accept", null],
      ["POST", "/api/v1/commercial-orders/order-1/contracts", {
        content_ref: "object://contracts/v1.pdf",
        content_digest: "sha256:contract",
        amount_minor: 9_000,
      }],
      ["POST", "/api/v1/commercial-orders/order-1/milestones/milestone-1/deliveries", {
        artifact_version_id: "video-v1",
        artifact_digest: "sha256:video-v1",
        note: "首版",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/deliveries/delivery-1/return", {
        reason: "片尾标识不符合合同",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/deliveries/delivery-2/accept", {
        evidence_ref: "object://acceptance/signature.json",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/milestones/milestone-1/disputes", {
        kind: "acceptance",
        reason: "签收证据主体不一致",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/disputes/dispute-1/resolve", {
        resolution: "证据已补正",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/settlements/settlement-1/freeze", {
        amount_minor: 10_000,
        currency: "CNY",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/settlements/settlement-1/resume", {
        amount_minor: 10_000,
        currency: "CNY",
      }],
      ["POST", "/api/v1/commercial-orders/order-1/settlements/settlement-1/pay", {
        amount_minor: 10_000,
        currency: "CNY",
      }],
    ] as const;

    expect(server.requests).toHaveLength(expected.length);
    expected.forEach(([method, url, body], index) => {
      const request = server.requests[index]!;
      expect(request.method).toBe(method);
      expect(request.url).toBe(url);
      expect(request.body === null ? null : JSON.parse(request.body)).toEqual(body);
      expect(request.headers.authorization).toBe("Bearer access-token-1");
      expect(request.headers["x-workspace-id"]).toBe("workspace-1");
      expect(request.headers["idempotency-key"]).toBe("idempotency-key-1");
      expect(request.headers["if-match"]).toBe('"7"');
      expect(request.headers["x-request-id"]).toBe("request-1");
    });
  });

  it("解析 FastAPI 错误信封并区分权限、版本冲突与幂等冲突", async () => {
    server.enqueueJson(403, { detail: { code: "FORBIDDEN" } }, { "X-Request-ID": "request-403" });
    server.enqueueJson(409, { detail: { code: "VERSION_CONFLICT" } }, { "X-Request-ID": "request-409" });
    server.enqueueJson(409, { detail: { code: "IDEMPOTENCY_CONFLICT" } });
    const client = createClient();
    const command = {
      ifMatch: commercialEtagFromVersion(7),
      idempotencyKey: "conflict-key",
    };

    await expect(client.getOrder("order-1")).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "forbidden",
      status: 403,
      code: "FORBIDDEN",
      requestId: "request-403",
      retryable: false,
      requiresRefresh: false,
    });
    await expect(client.acceptQuote("order-1", "quote-1", command)).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "conflict",
      status: 409,
      code: "VERSION_CONFLICT",
      conflict: "version",
      retryable: true,
      requiresRefresh: true,
    });
    await expect(client.acceptQuote("order-1", "quote-1", command)).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "conflict",
      status: 409,
      code: "IDEMPOTENCY_CONFLICT",
      conflict: "idempotency",
      retryable: false,
      requiresRefresh: false,
    });
  });

  it("拒绝缺失、非法或与响应版本不一致的 ETag", async () => {
    const order = commercialOrderFixture();
    server.enqueueJson(200, order);
    server.enqueueJson(200, order, { ETag: "not-an-etag" });
    server.enqueueJson(200, order, { ETag: '"8"' });
    const client = createClient();

    await expect(client.getOrder("order-1")).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "contract",
      code: "MISSING_ETAG",
    });
    await expect(client.getOrder("order-1")).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "contract",
      code: "INVALID_ETAG",
    });
    await expect(client.getOrder("order-1")).rejects.toMatchObject({
      name: "CommercialApiError",
      kind: "contract",
      code: "ETAG_VERSION_MISMATCH",
    });
  });

  it("暴露可判型的错误类供页面冲突恢复逻辑使用", () => {
    const error = new CommercialApiError({
      message: "版本冲突",
      kind: "conflict",
      status: 409,
      code: "VERSION_CONFLICT",
      requestId: "request-1",
      retryable: true,
      requiresRefresh: true,
      conflict: "version",
      details: null,
    });

    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("CommercialApiError");
  });
});
