import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, createIdentityClient, createMemoryTokenStore } from "./api";

const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("identity api client", () => {
  const fetcher = vi.fn<typeof fetch>();
  const tokens = createMemoryTokenStore();

  beforeEach(() => {
    fetcher.mockReset();
    tokens.clear();
  });

  it("按后端 envelope 注册并返回用户与个人工作区", async () => {
    fetcher.mockResolvedValueOnce(response({
      data: {
        user: { id: "u-1", email: "alice@example.com", displayName: "Alice" },
        workspace: { id: "w-1", name: "Alice 的空间", role: "OWNER" },
      },
      meta: { requestId: "req-1", serverTime: "2026-07-15T00:00:00Z" },
    }, 201));

    const client = createIdentityClient({ fetcher, tokenStore: tokens });
    const result = await client.register({
      email: "alice@example.com",
      password: "StrongPassword-123!",
      displayName: "Alice",
    });

    expect(result.workspace.role).toBe("OWNER");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/register", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ email: "alice@example.com", password: "StrongPassword-123!", displayName: "Alice" }),
    }));
  });

  it("登录后保存服务端令牌并用 Bearer 加载资料", async () => {
    fetcher
      .mockResolvedValueOnce(response({
        data: {
          accessToken: "access-real",
          refreshToken: "refresh-real",
          accessExpiresAt: "2026-07-15T01:00:00Z",
          refreshExpiresAt: "2026-07-22T00:00:00Z",
        },
        meta: { requestId: "req-login", serverTime: "2026-07-15T00:00:00Z" },
      }))
      .mockResolvedValueOnce(response({
        data: { id: "u-1", email: "alice@example.com", displayName: "Alice" },
        meta: { requestId: "req-profile", serverTime: "2026-07-15T00:00:01Z" },
      }));

    const client = createIdentityClient({ fetcher, tokenStore: tokens });
    await client.login({ email: "alice@example.com", password: "secret", deviceName: "Web" });
    const profile = await client.getProfile();

    expect(profile.email).toBe("alice@example.com");
    expect(tokens.get()?.accessToken).toBe("access-real");
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/account/profile", expect.objectContaining({
      headers: expect.objectContaining({ authorization: "Bearer access-real" }),
    }));
  });

  it("401 清除令牌并保留安全错误码，不泄露身份猜测", async () => {
    tokens.set({
      accessToken: "expired",
      refreshToken: "refresh",
      accessExpiresAt: "2026-07-14T00:00:00Z",
      refreshExpiresAt: "2026-07-20T00:00:00Z",
    });
    fetcher.mockResolvedValueOnce(response({
      error: { code: "UNAUTHENTICATED", message: "登录态无效或已过期", details: [], retryable: false },
      meta: { requestId: "req-expired", serverTime: "2026-07-15T00:00:00Z" },
    }, 401));

    const client = createIdentityClient({ fetcher, tokenStore: tokens });
    await expect(client.getProfile()).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "UNAUTHENTICATED",
      requestId: "req-expired",
    });
    expect(tokens.get()).toBeNull();
  });

  it("切换工作区携带幂等键并返回服务端最终上下文", async () => {
    tokens.set({ accessToken: "a", refreshToken: "r", accessExpiresAt: "x", refreshExpiresAt: "y" });
    fetcher.mockResolvedValueOnce(response({
      data: {
        user: { id: "u-1", email: "a@b.com", displayName: "A" },
        currentWorkspace: { id: "w-2", name: "团队", slug: "team", status: "ACTIVE", role: "MEMBER", version: 2 },
        workspaces: [],
      },
      meta: { requestId: "req-select", serverTime: "2026-07-15T00:00:00Z" },
    }));

    const client = createIdentityClient({ fetcher, tokenStore: tokens, createIdempotencyKey: () => "idem-1" });
    const context = await client.selectWorkspace("w-2");

    expect(context.currentWorkspace?.id).toBe("w-2");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/session/context/workspace", expect.objectContaining({
      method: "PUT",
      headers: expect.objectContaining({ "idempotency-key": "idem-1" }),
      body: JSON.stringify({ workspaceId: "w-2" }),
    }));
  });

  it("非 JSON 或畸形成功响应按契约错误处理", async () => {
    fetcher.mockResolvedValueOnce(new Response("gateway", { status: 502 }));
    const client = createIdentityClient({ fetcher, tokenStore: tokens });
    await expect(client.login({ email: "a@b.com", password: "x" })).rejects.toBeInstanceOf(ApiError);
  });
});
