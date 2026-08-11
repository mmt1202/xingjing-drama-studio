import { describe, expect, it } from "vitest";

import {
  collaborationCommerceRouteMap,
  collaborationCommerceRouteMounts,
  collaborationCommerceRoutes,
} from "./routes";

describe("M10-M14 页面信息架构", () => {
  it("完整导出 46 个稳定页面 ID 与不含 html 后缀的主线路由", () => {
    expect(collaborationCommerceRoutes).toHaveLength(46);
    expect(new Set(collaborationCommerceRoutes.map((route) => route.id)).size).toBe(46);
    expect(collaborationCommerceRouteMounts).toHaveLength(46);
    expect(collaborationCommerceRoutes.every((route) => route.path.startsWith("/") && !route.path.endsWith(".html"))).toBe(true);
  });

  it("把五个模块的代表页映射到正式端点、上下文和服务端权限", () => {
    expect(collaborationCommerceRouteMap.get("TM-010")).toMatchObject({
      path: "/team/team-members",
      module: "M10",
      loadEndpoint: "/workspaces/{workspaceId}/members",
      requiredContext: ["workspaceId"],
      viewPermission: "workspace.member.view",
    });
    expect(collaborationCommerceRouteMap.get("TM-001")).toMatchObject({
      module: "M11",
      loadEndpoint: "/workspaces/{workspaceId}/billing",
      viewPermission: "billing.view",
    });
    expect(collaborationCommerceRouteMap.get("CL-005")).toMatchObject({
      module: "M12",
      audience: "client",
      loadEndpoint: "/review-links/{reviewToken}/context",
      requiredContext: ["reviewToken"],
    });
    expect(collaborationCommerceRouteMap.get("CR-111")).toMatchObject({
      module: "M13",
      path: "/creator/template-market",
      loadEndpoint: "/templates",
    });
    expect(collaborationCommerceRouteMap.get("CR-014")).toMatchObject({
      module: "M14",
      path: "/creator/commercial-order-detail",
      loadEndpoint: "commercial-client",
    });
  });

  it("客户上下文只声明审片安全动作，不暴露生产编辑权限", () => {
    const clientRoutes = collaborationCommerceRoutes.filter((route) => route.audience === "client");
    const clientActions = clientRoutes.flatMap((route) => route.actions.map((action) => action.id));

    expect(clientRoutes.map((route) => route.id)).toEqual(["CL-001", "CL-002", "CL-003", "CL-004", "CL-005"]);
    expect(clientActions.every((action) => ["verify", "comment", "approve", "reject"].includes(action))).toBe(true);
    expect(clientRoutes.every((route) => route.actions.every((action) => action.clientSafe))).toBe(true);
  });
});
