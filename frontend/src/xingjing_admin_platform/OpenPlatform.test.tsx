import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { OpenPlatform } from "./OpenPlatform";
import type { AdminApi } from "./types";

it("API Key 只在创建响应后展示一次，关闭后仅保留遮罩值，并支持轮换和撤销", async () => {
  const api: AdminApi = {
    getContext: vi.fn().mockResolvedValue({
      actor: { id: "staff-1", displayName: "技术运维", role: "api_admin" },
      workspace: { id: "ws-1", name: "平台空间" },
      permissions: ["admin.api.view", "admin.api.manage"],
      dataScope: "global",
    }),
    list: vi.fn().mockResolvedValue({
      items: [{ id: "key-1", name: "制作系统", status: "active", version: 1, updatedAt: "2026-07-15", maskedSecret: "xj_live_••••7M2Q", scopes: ["video.generate"], webhooks: [{ id: "wh-1", url: "https://example.com/hook", status: "active", version: 2 }] }],
      page: 1, pageSize: 20, total: 1,
    }),
    act: vi.fn().mockResolvedValue({ requestId: "req-key", status: "succeeded", oneTimeSecret: "xj_live_once_secret" }),
  };

  render(<OpenPlatform api={api} />);
  expect(await screen.findByText("xj_live_••••7M2Q")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "创建 API Key" }));
  expect(await screen.findByText("xj_live_once_secret")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "我已安全保存" }));
  expect(screen.queryByText("xj_live_once_secret")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "轮换制作系统" }));
  fireEvent.click(screen.getByRole("button", { name: "撤销制作系统" }));
  expect(api.act).toHaveBeenCalledWith("api", expect.objectContaining({ action: "rotate_key", objectId: "key-1", version: 1 }));
  expect(api.act).toHaveBeenCalledWith("api", expect.objectContaining({ action: "revoke_key", objectId: "key-1", version: 1 }));
});
