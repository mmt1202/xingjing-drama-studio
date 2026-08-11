import { describe, expect, it } from "vitest";

import { assetsStoryboardRouteExports } from "./routes";

describe("M04/M05 路由导出", () => {
  it("为资产、分镜和故事板页面提供无重复的主线接线点", () => {
    const paths = assetsStoryboardRouteExports.map((route) => route.path);

    expect(new Set(paths).size).toBe(paths.length);
    expect(paths).toEqual(expect.arrayContaining([
      "/workspaces/:workspaceId/assets",
      "/workspaces/:workspaceId/projects/:projectId/assets/extract",
      "/workspaces/:workspaceId/projects/:projectId/assets/:assetId",
      "/workspaces/:workspaceId/projects/:projectId/shots",
      "/workspaces/:workspaceId/projects/:projectId/shots/batch",
      "/workspaces/:workspaceId/projects/:projectId/shots/quality",
      "/workspaces/:workspaceId/projects/:projectId/shots/import",
      "/workspaces/:workspaceId/projects/:projectId/shots/export",
      "/workspaces/:workspaceId/projects/:projectId/shots/smart",
      "/workspaces/:workspaceId/projects/:projectId/storyboard",
      "/workspaces/:workspaceId/projects/:projectId/shots/compare",
    ]));
    expect(assetsStoryboardRouteExports.every((route) => typeof route.component === "function")).toBe(true);
  });
});
