import { describe, expect, it, vi } from "vitest";

import { PostproductionApiError, createPostproductionApi } from "./api";

describe("星镜后期 typed API", () => {
  it("提交合成携带工作区上下文、幂等键和输入版本", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ id: "render-1", status: "queued", version: 4 }), {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );
    const api = createPostproductionApi({ fetcher, createIdempotencyKey: () => "idem-1" });

    await api.createRenderTask(
      { workspaceId: "ws-1", projectId: "project/一", episodeId: "ep-1" },
      { timelineId: "timeline-1", version: 3 },
    );

    expect(fetcher).toHaveBeenCalledWith("/api/v1/projects/project%2F%E4%B8%80/render-tasks", {
      method: "POST",
      headers: expect.objectContaining({
        "Content-Type": "application/json",
        "Idempotency-Key": "idem-1",
        "X-Workspace-Id": "ws-1",
      }),
      body: JSON.stringify({ timeline_id: "timeline-1", version: 3, episode_id: "ep-1" }),
      signal: undefined,
    });
  });

  it("把 409 响应保留为可恢复的版本冲突", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ code: "VERSION_CONFLICT", message: "版本已变化", request_id: "req-9" }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );
    const api = createPostproductionApi({ fetcher });

    await expect(
      api.performComplianceAction(
        { workspaceId: "ws-1", projectId: "p-1" },
        { action: "appeal", recordId: "c-1", version: 7, reason: "证据已补齐" },
      ),
    ).rejects.toMatchObject({ status: 409, code: "VERSION_CONFLICT", requestId: "req-9" } satisfies Partial<PostproductionApiError>);
  });
});
