import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PostproductionWorkspace, zhCNPostproductionCopy } from "./PostproductionWorkspace";
import type { PostproductionApi } from "./api";

const scope = { workspaceId: "ws-1", projectId: "p-1", episodeId: "ep-1" };
const context = { userId: "u-1", workspaceId: "ws-1", permissions: ["final.view", "final.manage", "compliance.view", "compliance.manage", "export.view", "export.manage"], featureFlags: [] };

function api(overrides: Partial<PostproductionApi> = {}): PostproductionApi {
  return {
    getSessionContext: vi.fn().mockResolvedValue(context),
    listTimelines: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    createRenderTask: vi.fn(),
    listCompliance: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    performComplianceAction: vi.fn(),
    listExports: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    createExport: vi.fn(),
    ...overrides,
  };
}

describe("后期与合规交付工作区", () => {
  it("403 时不泄露项目和媒体数据，并允许重新校验权限", async () => {
    const client = api({ getSessionContext: vi.fn().mockRejectedValue(Object.assign(new Error("forbidden"), { status: 403 })) });
    render(<PostproductionWorkspace api={client} scope={scope} copy={zhCNPostproductionCopy} exportPreset={{ target: "douyin", format: "mp4" }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("没有访问权限");
    expect(screen.queryByText("p-1")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新校验权限" }));
    expect(client.getSessionContext).toHaveBeenCalledTimes(2);
  });

  it("合规阻断时展示风险、解除条件和申诉，但不提供导出动作", async () => {
    const client = api({
      listCompliance: vi.fn().mockResolvedValue({ items: [{ id: "c-1", version: 2, ruleVersion: "r-9", projectVersion: 7, status: "blocked", riskLevel: "high", evidence: [], owner: "法务", unblockCondition: "补充音乐授权", updatedAt: "2026-07-15" }], total: 1 }),
      listExports: vi.fn().mockResolvedValue({ items: [{ id: "e-1", version: 1, target: "douyin", format: "mp4", projectVersion: 7, complianceStatus: "blocked", status: "blocked", updatedAt: "2026-07-15" }], total: 1 }),
    });
    render(<PostproductionWorkspace api={client} scope={scope} copy={zhCNPostproductionCopy} exportPreset={{ target: "douyin", format: "mp4" }} />);
    expect(await screen.findByText(/补充音乐授权/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交申诉" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "创建正式导出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  });

  it("通过合规后可提交幂等导出，处理中禁用重复提交并在成功后重读", async () => {
    let resolve!: (value: { id: string; status: "queued"; version: number }) => void;
    const createExport = vi.fn().mockImplementation(() => new Promise((r) => { resolve = r; }));
    const listExports = vi.fn().mockResolvedValue({ items: [], total: 0 });
    const client = api({
      listCompliance: vi.fn().mockResolvedValue({ items: [{ id: "c-1", version: 2, ruleVersion: "r-9", projectVersion: 7, status: "passed", riskLevel: "low", evidence: [], updatedAt: "2026-07-15" }], total: 1 }),
      createExport, listExports,
    });
    render(<PostproductionWorkspace api={client} scope={scope} copy={zhCNPostproductionCopy} exportPreset={{ target: "douyin", format: "mp4" }} />);
    const button = await screen.findByRole("button", { name: "创建正式导出" });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    resolve({ id: "e-2", status: "queued", version: 1 });
    await waitFor(() => expect(listExports).toHaveBeenCalledTimes(2));
    expect(createExport).toHaveBeenCalledTimes(1);
  });

  it("409 冲突不覆盖并提供从服务端恢复", async () => {
    const createRenderTask = vi.fn().mockRejectedValue(Object.assign(new Error("conflict"), { status: 409, code: "VERSION_CONFLICT" }));
    const listTimelines = vi.fn().mockResolvedValue({ items: [{ id: "t-1", version: 3, updatedAt: "now", tracks: [], finalVersions: [] }], total: 1 });
    const client = api({ createRenderTask, listTimelines });
    render(<PostproductionWorkspace api={client} scope={scope} copy={zhCNPostproductionCopy} />);
    await userEvent.click(await screen.findByRole("button", { name: "提交最终合成" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("版本冲突");
    await userEvent.click(screen.getByRole("button", { name: "读取服务端最新版本" }));
    await waitFor(() => expect(listTimelines).toHaveBeenCalledTimes(2));
  });
});
