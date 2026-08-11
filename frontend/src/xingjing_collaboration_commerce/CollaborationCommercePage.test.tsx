import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CollaborationCommercePage } from "./CollaborationCommercePage";
import type {
  CollaborationCommerceApi,
  CommercePageSnapshot,
  SessionContext,
} from "./types";

const session: SessionContext = {
  user: { id: "user-1", displayName: "团队管理员" },
  activeWorkspace: { id: "workspace-1", name: "星镜内容团队" },
  workspaces: [{ id: "workspace-1", name: "星镜内容团队" }],
  permissions: [
    "workspace.member.view",
    "workspace.member.manage",
    "billing.view",
    "review.view",
    "review.comment",
    "review.approve",
  ],
  featureFlags: {},
};

const members: CommercePageSnapshot = {
  requestId: "request-page-1",
  serverTime: "2026-07-16T08:00:00Z",
  items: [{
    id: "member-1",
    name: "林晚",
    email: "linwan@example.test",
    role: "项目负责人",
    dataScope: "project:assigned",
    seat: "creator",
    status: "active",
    version: 3,
    updatedAt: "2026-07-16T07:00:00Z",
  }],
  summary: [{ key: "active", label: "活跃成员", value: 1, format: "integer" }],
  workflow: [{ id: "invite", label: "已邀请", status: "done", occurredAt: "2026-07-14T08:00:00Z" }],
  nextPageToken: null,
};

function api(overrides: Partial<CollaborationCommerceApi> = {}): CollaborationCommerceApi {
  return {
    getSessionContext: vi.fn().mockResolvedValue(session),
    loadPage: vi.fn().mockResolvedValue(members),
    executeAction: vi.fn().mockResolvedValue({
      requestId: "request-action-1",
      status: "succeeded",
      failures: [],
      retryableIds: [],
    }),
    ...overrides,
  };
}

describe("M10-M14 生产级页面状态", () => {
  it("先验证服务端会话权限，再呈现工作区数据和服务端证据轨", async () => {
    const client = api();
    render(<CollaborationCommercePage pageId="TM-010" api={client} context={{ workspaceId: "workspace-1" }} />);

    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("正在核对会话、权限与数据范围")).toBeInTheDocument();
    expect(await screen.findByText("林晚")).toBeInTheDocument();
    expect(screen.getByText("星镜内容团队")).toBeInTheDocument();
    expect(screen.getByText("已邀请")).toBeInTheDocument();
    expect(client.getSessionContext).toHaveBeenCalledTimes(1);
    expect(client.loadPage).toHaveBeenCalledTimes(1);
  });

  it("服务端拒绝后不泄露对象，并提供安全的权限恢复说明", async () => {
    const denied = api({
      getSessionContext: vi.fn().mockRejectedValue(Object.assign(new Error("林晚的保密项目"), {
        status: 403,
        code: "FORBIDDEN",
        requestId: "request-denied",
      })),
    });
    render(<CollaborationCommercePage pageId="TM-010" api={denied} context={{ workspaceId: "workspace-1" }} />);

    expect(await screen.findByText("无权访问此团队能力")).toBeInTheDocument();
    expect(screen.queryByText(/保密项目/)).not.toBeInTheDocument();
    expect(screen.getByText(/request-denied/)).toBeInTheDocument();
    expect(denied.loadPage).not.toHaveBeenCalled();
  });

  it("空状态与失败状态均由服务端结果驱动并提供重新读取", async () => {
    const loadPage = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error("down"), { status: 503, code: "SERVICE_UNAVAILABLE", requestId: "request-down" }))
      .mockResolvedValueOnce({ ...members, items: [], summary: [], workflow: [] });
    const client = api({ loadPage });
    render(<CollaborationCommercePage pageId="TM-001" api={client} context={{ workspaceId: "workspace-1" }} />);

    expect(await screen.findByText("团队财务数据暂时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取服务端状态" }));
    expect(await screen.findByText("当前数据范围内还没有团队账单数据")).toBeInTheDocument();
    expect(loadPage).toHaveBeenCalledTimes(2);
  });

  it("成员邀请必须二次确认，提交后重新读取服务端最终状态", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn().mockResolvedValueOnce(members).mockResolvedValueOnce({
      ...members,
      requestId: "request-page-2",
      items: [...members.items, {
        id: "member-2",
        name: "待加入成员",
        email: "new@example.test",
        role: "成员",
        status: "invited",
        version: 1,
        updatedAt: "2026-07-16T08:05:00Z",
      }],
    });
    const executeAction = vi.fn().mockResolvedValue({ requestId: "request-action-2", status: "succeeded", failures: [], retryableIds: [] });
    const client = api({ loadPage, executeAction });
    render(<CollaborationCommercePage pageId="TM-010" api={client} context={{ workspaceId: "workspace-1" }} />);
    await screen.findByText("林晚");

    await user.click(screen.getByRole("button", { name: "邀请成员" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认邀请成员" });
    await user.type(within(dialog).getByLabelText("成员邮箱"), "new@example.test");
    await user.click(within(dialog).getByRole("button", { name: "确认邀请成员" }));

    expect(screen.getByText("正在提交邀请成员")).toBeInTheDocument();
    expect(await screen.findByText("待加入成员")).toBeInTheDocument();
    expect(executeAction).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ id: "inviteMember" }), { workspaceId: "workspace-1" }, expect.objectContaining({
      payload: { email: "new@example.test" },
    }), expect.anything());
    expect(loadPage).toHaveBeenCalledTimes(2);
    expect(screen.getByText("操作已确认，已读取服务端最终状态")).toBeInTheDocument();
  });

  it("409 冲突不会覆盖现有数据，并允许读取最新版本", async () => {
    const user = userEvent.setup();
    const loadPage = vi.fn().mockResolvedValue(members);
    const executeAction = vi.fn().mockRejectedValue(Object.assign(new Error("conflict"), {
      status: 409,
      code: "VERSION_CONFLICT",
      requestId: "request-conflict",
    }));
    render(<CollaborationCommercePage pageId="TM-010" api={api({ loadPage, executeAction })} context={{ workspaceId: "workspace-1" }} />);
    await screen.findByText("林晚");

    await user.click(screen.getByRole("button", { name: "停用成员" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认停用成员" });
    await user.type(within(dialog).getByLabelText("操作原因"), "岗位调整");
    await user.click(within(dialog).getByRole("button", { name: "确认停用成员" }));

    expect(await screen.findByText("数据已被其他协作者更新")).toBeInTheDocument();
    expect(screen.getByText("林晚")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "读取最新版本" }));
    await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(2));
  });

  it("部分失败保留成功项，并可只重试失败对象", async () => {
    const user = userEvent.setup();
    const executeAction = vi.fn()
      .mockResolvedValueOnce({
        requestId: "request-partial",
        status: "partial",
        failures: [{ id: "member-2", code: "SEAT_LIMIT", message: "席位已满" }],
        retryableIds: ["member-2"],
      })
      .mockResolvedValueOnce({ requestId: "request-retry", status: "succeeded", failures: [], retryableIds: [] });
    render(<CollaborationCommercePage pageId="TM-010" api={api({ executeAction })} context={{ workspaceId: "workspace-1" }} />);
    await screen.findByText("林晚");

    await user.click(screen.getByRole("button", { name: "停用成员" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认停用成员" });
    await user.type(within(dialog).getByLabelText("操作原因"), "权限收回");
    await user.click(within(dialog).getByRole("button", { name: "确认停用成员" }));

    expect(await screen.findByText("部分对象处理失败")).toBeInTheDocument();
    expect(screen.getByText("席位已满")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅重试失败项" }));
    expect(await screen.findByText("操作已确认，已读取服务端最终状态")).toBeInTheDocument();
    expect(executeAction).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ id: "retryFailed" }), { workspaceId: "workspace-1" }, expect.objectContaining({
      payload: { ids: ["member-2"] },
    }), expect.anything());
  });

  it("客户审片页只加载受控外链上下文，不请求生产会话或展示生产编辑动作", async () => {
    const getSessionContext = vi.fn();
    const loadPage = vi.fn().mockResolvedValue({
      requestId: "request-review",
      serverTime: "2026-07-16T08:00:00Z",
      items: [],
      detail: {
        id: "review-1",
        title: "第 3 集成片",
        visibleVersion: "v12",
        mediaUrl: "https://media.example.test/review-1.mp4",
        watermark: "客户审片",
        approvalStatus: "pending",
        expiresAt: "2026-07-19T08:00:00Z",
        version: 12,
        updatedAt: "2026-07-16T07:00:00Z",
      },
      summary: [],
      workflow: [],
      nextPageToken: null,
    });
    render(<CollaborationCommercePage pageId="CL-005" api={api({ getSessionContext, loadPage })} context={{ reviewToken: "review-token-abcdefghijklmnopqrstuvwxyz" }} />);

    expect(await screen.findByText("第 3 集成片")).toBeInTheDocument();
    expect(screen.getByText("客户审片播放器")).toBeInTheDocument();
    expect(screen.getByText("客户访问上下文仅允许播放、批注与验收")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /编辑项目|修改成片|重新生成/ })).not.toBeInTheDocument();
    expect(getSessionContext).not.toHaveBeenCalled();
  });

  it("M14 未注入 CommercialClient 适配器时不回落调用旧的通用商单 API", async () => {
    const legacy = api();
    render(<CollaborationCommercePage pageId="CR-015" api={legacy} context={{ workspaceId: "workspace-1" }} />);

    expect(await screen.findByText("商单数据暂时不可用")).toBeInTheDocument();
    expect(legacy.getSessionContext).not.toHaveBeenCalled();
    expect(legacy.loadPage).not.toHaveBeenCalled();
  });
});
