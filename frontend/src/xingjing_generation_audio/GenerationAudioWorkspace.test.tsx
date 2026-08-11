import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GenerationAudioWorkspace } from "./GenerationAudioWorkspace";

function api(overrides: Record<string, unknown> = {}) {
  return {
    session: vi.fn().mockResolvedValue({ user: { id: "u", name: "用户" }, workspace: { id: "w", name: "动画工作室" }, permissions: ["generation.manage", "audio.manage"], features: {} }),
    models: vi.fn().mockResolvedValue({ items: [{ id: "m1", name: "视频模型", version: "v2", capabilities: ["video"], healthy: true, estimated_cost: 8, currency: "CNY" }], total: 1, page: 1, page_size: 20 }),
    tasks: vi.fn().mockResolvedValue({ items: [{ id: "t1", kind: "video", model_id: "m1", status: "queued", queue_position: 3, version: 1 }], total: 1, page: 1, page_size: 20 }),
    audioTracks: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
    estimate: vi.fn().mockResolvedValue({ amount: 8, currency: "CNY" }), createTask: vi.fn().mockResolvedValue({}), actionTask: vi.fn().mockResolvedValue({}), audioAction: vi.fn().mockResolvedValue({}), ...overrides,
  };
}

describe("媒体生成与音频工作区", () => {
  it("显示真实上下文、排队状态与成本", async () => {
    render(<GenerationAudioWorkspace projectId="p1" projectName="第一集" api={api() as never} />);
    expect(screen.getByLabelText("媒体生产工作区")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByText("动画工作室 / 第一集")).toBeInTheDocument();
    expect(screen.getByText(/排队中/)).toBeInTheDocument();
    expect(screen.getByText(/前方 3 项/)).toBeInTheDocument();
    expect(screen.getByText(/CNY 8/)).toBeInTheDocument();
  });

  it("防止快速双击产生重复提交", async () => {
    let resolve!: () => void;
    const createTask = vi.fn(() => new Promise<void>((done) => { resolve = done; }));
    const client = api({ createTask });
    render(<GenerationAudioWorkspace projectId="p1" api={client as never} />);
    await screen.findByText(/动画工作室/);
    await userEvent.type(screen.getByLabelText("提示词"), "雨夜追逐");
    const submit = screen.getByRole("button", { name: "提交生成" });
    await userEvent.dblClick(submit);
    expect(createTask).toHaveBeenCalledTimes(1);
    resolve();
    await waitFor(() => expect(client.tasks).toHaveBeenCalledTimes(2));
  });

  it("403 时不泄露项目与任务字段", async () => {
    const forbidden = Object.assign(new Error("forbidden"), { detail: { status: 403, code: "FORBIDDEN", message: "禁止" } });
    Object.setPrototypeOf(forbidden, (await import("./api")).XingjingApiError.prototype);
    render(<GenerationAudioWorkspace projectId="secret" projectName="机密项目" api={api({ session: vi.fn().mockRejectedValue(forbidden) }) as never} />);
    expect(await screen.findByText("无权访问")).toBeInTheDocument();
    expect(screen.queryByText("机密项目")).not.toBeInTheDocument();
  });
});
