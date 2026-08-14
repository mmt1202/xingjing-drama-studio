import { afterEach, describe, expect, it, vi } from "vitest";

import { createGenerationAudioApi } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("audio API contract", () => {
  it("uses the trusted bearer session without a forgeable workspace header", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { tasks: [] }, meta: {} }), { status: 200 }));
    const api = createGenerationAudioApi({ workspaceId: "ignored", fetcher, getAccessToken: () => "access-token" });

    await api.audioTasks("project-9");

    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer access-token");
    expect(headers.has("X-Workspace-Id")).toBe(false);
  });

  it("sends idempotency and optimistic version headers on writes", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { result: { id: "t1" } }, meta: {} }), { status: 200 }));
    const api = createGenerationAudioApi({ fetcher });

    await api.audioAction("p1", { action: "cancel_media_task", taskId: "t1" }, "key-1", 7);

    const headers = new Headers(fetcher.mock.calls[0]?.[1]?.headers);
    expect(headers.get("Idempotency-Key")).toBe("key-1");
    expect(headers.get("If-Match")).toBe("7");
  });

  it("normalizes a 409 into the current recoverable conflict code", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "AUDIO_VERSION_OR_IDEMPOTENCY_CONFLICT" }, meta: { requestId: "req-1" } }), { status: 409 }));
    const api = createGenerationAudioApi({ fetcher });

    await expect(api.audioAction("p1", {}, "k", 2)).rejects.toMatchObject({
      detail: { status: 409, code: "AUDIO_VERSION_OR_IDEMPOTENCY_CONFLICT", requestId: "req-1" },
    });
  });
});
