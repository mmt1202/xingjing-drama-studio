import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AdminPlatform } from "./AdminPlatform";
import type { AdminApi } from "./types";

const context = {
  actor: { id: "staff-1", displayName: "审核员", role: "reviewer" },
  workspace: { id: "ws-1", name: "平台空间" },
  permissions: [
    "admin.review.view", "admin.review.manage",
    "admin.operations.view", "admin.operations.manage",
    "admin.finance.view", "admin.finance.manage",
    "admin.business.view", "admin.business.manage",
  ],
  dataScope: "assigned" as const,
};

function api(overrides: Partial<AdminApi> = {}): AdminApi {
  return {
    getContext: vi.fn().mockResolvedValue(context),
    list: vi.fn().mockResolvedValue({
      items: [{ id: "review-1", name: "高风险视频", status: "pending", version: 3, updatedAt: "2026-07-15T10:00:00Z", sensitive: { mobile: "13800138000" } }],
      page: 1,
      pageSize: 20,
      total: 1,
    }),
    act: vi.fn().mockResolvedValue({ requestId: "req-1", status: "succeeded" }),
    ...overrides,
  };
}

describe("管理后台页面闭环", () => {
  it("加载真实上下文和数据，并默认遮罩敏感字段", async () => {
    render(<AdminPlatform routeId="AD-036" api={api()} />);

    expect(screen.getByText("正在验证后台会话与数据范围…")).toBeInTheDocument();
    expect(await screen.findByText("高风险视频")).toBeInTheDocument();
    expect(screen.getByText("138****8000")).toBeInTheDocument();
    expect(screen.getByText("平台空间 · assigned")).toBeInTheDocument();
  });

  it("权限拒绝不渲染服务端对象信息", async () => {
    const denied = api({
      getContext: vi.fn().mockRejectedValue(Object.assign(new Error("secret object: 剧本A"), { status: 403, code: "FORBIDDEN" })),
    });
    render(<AdminPlatform routeId="AD-030" api={denied} />);

    expect(await screen.findByText("无权访问此后台能力")).toBeInTheDocument();
    expect(screen.queryByText(/剧本A/)).not.toBeInTheDocument();
    expect(denied.list).not.toHaveBeenCalled();
  });

  it("空状态和服务失败均提供真实重新读取动作", async () => {
    const list = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("down"), { status: 503, code: "SERVICE_UNAVAILABLE" }))
      .mockResolvedValueOnce({ items: [], page: 1, pageSize: 20, total: 0 });
    render(<AdminPlatform routeId="AD-043" api={api({ list })} />);

    expect(await screen.findByText("后台服务暂时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("当前数据范围内没有工单记录")).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("写操作显示处理中、成功，并在 409 时要求刷新服务端版本", async () => {
    let rejectAction: ((reason: unknown) => void) | undefined;
    const act = vi.fn().mockImplementation(() => new Promise((_resolve, reject) => { rejectAction = reject; }));
    const actionApi = api({ act });
    render(<AdminPlatform routeId="AD-018" api={actionApi} />);
    await screen.findByText("高风险视频");

    fireEvent.click(screen.getByRole("button", { name: "执行审批" }));
    expect(screen.getByText("正在提交审批…")).toBeInTheDocument();
    rejectAction?.(Object.assign(new Error("conflict"), { status: 409, code: "VERSION_CONFLICT" }));
    expect(await screen.findByText("数据已被其他管理员更新")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "读取最新版本" }));
    await waitFor(() => expect(actionApi.list).toHaveBeenCalledTimes(2));
  });
});
