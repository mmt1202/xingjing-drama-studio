import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, type IdentityClient, type SessionContext } from "./api";
import { AuthForm } from "./AuthForm";
import { SessionGate } from "./SessionGate";
import { WorkspaceSelector } from "./WorkspaceSelector";

const context: SessionContext = {
  user: { id: "u-1", email: "alice@example.com", displayName: "Alice" },
  currentWorkspace: null,
  workspaces: [
    { id: "w-1", name: "个人工作区", slug: "alice", status: "ACTIVE", role: "OWNER", version: 1 },
    { id: "w-2", name: "制作团队", slug: "studio", status: "ACTIVE", role: "MEMBER", version: 3 },
  ],
};

const client = (overrides: Partial<IdentityClient> = {}): IdentityClient => ({
  register: vi.fn(),
  login: vi.fn(),
  getProfile: vi.fn(),
  getSessionContext: vi.fn(),
  selectWorkspace: vi.fn(),
  ...overrides,
});

describe("AuthForm", () => {
  it("提交登录并将真实会话结果交给调用方", async () => {
    const login = vi.fn().mockResolvedValue({
      accessToken: "server-token", refreshToken: "server-refresh", accessExpiresAt: "x", refreshExpiresAt: "y",
    });
    const onAuthenticated = vi.fn();
    render(<AuthForm mode="login" client={client({ login })} onAuthenticated={onAuthenticated} />);

    await userEvent.type(screen.getByLabelText("邮箱"), "alice@example.com");
    await userEvent.type(screen.getByLabelText("密码"), "password");
    fireEvent.submit(screen.getByRole("form", { name: "登录" }));

    expect(screen.getByRole("button", { name: "正在登录…" })).toBeDisabled();
    await waitFor(() => expect(login).toHaveBeenCalledWith(expect.objectContaining({
      email: "alice@example.com", password: "password",
    })));
    expect(onAuthenticated).toHaveBeenCalledWith(expect.objectContaining({ accessToken: "server-token" }));
  });

  it("注册采集昵称且服务端失败后保留输入并展示请求 ID", async () => {
    const register = vi.fn().mockRejectedValue(new ApiError({
      status: 409, code: "EMAIL_ALREADY_EXISTS", message: "无法完成注册", retryable: false, requestId: "req-register",
    }));
    render(<AuthForm mode="register" client={client({ register })} onRegistered={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("昵称"), "Alice");
    await userEvent.type(screen.getByLabelText("邮箱"), "alice@example.com");
    await userEvent.type(screen.getByLabelText("密码"), "StrongPassword-123!");
    await userEvent.click(screen.getByRole("button", { name: "创建账号" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法完成注册");
    expect(screen.getByRole("alert")).toHaveTextContent("req-register");
    expect(screen.getByLabelText("邮箱")).toHaveValue("alice@example.com");
  });
});

describe("SessionGate", () => {
  it("加载期间保留页面结构，成功后渲染服务端上下文", async () => {
    let resolve!: (value: SessionContext) => void;
    const promise = new Promise<SessionContext>((done) => { resolve = done; });
    render(
      <SessionGate client={client({ getSessionContext: vi.fn(() => promise) })}>
        {(value) => <p>你好，{value.user.displayName}</p>}
      </SessionGate>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("正在加载工作区…");
    resolve(context);
    expect(await screen.findByText("你好，Alice")).toBeInTheDocument();
  });

  it("403 显示权限不足且不渲染受保护内容", async () => {
    const error = new ApiError({ status: 403, code: "FORBIDDEN", message: "禁止访问", retryable: false, requestId: "req-403" });
    render(
      <SessionGate client={client({ getSessionContext: vi.fn().mockRejectedValue(error) })}>
        {() => <p>机密工作区名称</p>}
      </SessionGate>,
    );
    expect(await screen.findByText("权限不足")).toBeInTheDocument();
    expect(screen.queryByText("机密工作区名称")).not.toBeInTheDocument();
  });

  it("401 显示会话过期并提供重新登录动作", async () => {
    const onSessionExpired = vi.fn();
    const error = new ApiError({ status: 401, code: "UNAUTHENTICATED", message: "expired", retryable: false });
    render(
      <SessionGate client={client({ getSessionContext: vi.fn().mockRejectedValue(error) })} onSessionExpired={onSessionExpired}>
        {() => null}
      </SessionGate>,
    );
    expect(await screen.findByText("会话已过期")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新登录" }));
    expect(onSessionExpired).toHaveBeenCalledOnce();
  });
});

describe("WorkspaceSelector", () => {
  it("展示可访问工作区、角色、状态并以切换响应为准", async () => {
    const selected = { ...context, currentWorkspace: context.workspaces[1] };
    let finish!: (value: SessionContext) => void;
    const selectWorkspace = vi.fn(() => new Promise<SessionContext>((resolve) => { finish = resolve; }));
    const onSelected = vi.fn();
    render(<WorkspaceSelector context={context} client={client({ selectWorkspace })} onSelected={onSelected} />);

    const team = screen.getByRole("listitem", { name: "制作团队" });
    expect(within(team).getByText("MEMBER")).toBeInTheDocument();
    await userEvent.click(within(team).getByRole("button", { name: "进入制作团队" }));

    expect(within(team).getByRole("button", { name: "正在进入…" })).toBeDisabled();
    finish(selected);
    await waitFor(() => expect(onSelected).toHaveBeenCalledWith(selected));
  });

  it("空工作区与可重试失败均提供明确恢复动作", async () => {
    const retry = vi.fn().mockRejectedValueOnce(new ApiError({
      status: 503, code: "SERVICE_UNAVAILABLE", message: "服务暂时不可用", retryable: true,
    }));
    render(<WorkspaceSelector context={{ ...context, workspaces: [] }} client={client({ selectWorkspace: retry })} />);
    expect(screen.getByText("暂无可访问的工作区")).toBeInTheDocument();
  });
});
