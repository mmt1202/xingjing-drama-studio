import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AssetsStoryboardApiError } from "./api";
import type { AssetsStoryboardPort, SessionContext, ShotItem } from "./contracts";
import { StoryboardWorkspace } from "./StoryboardWorkspace";

const scope = { workspaceId: "workspace-1", projectId: "project-1", episodeId: "episode-1" };
const context: SessionContext = {
  user: { id: "user-1", name: "林导" },
  workspace: { id: "workspace-1", name: "镜像工作室" },
  project: { id: "project-1", name: "雾港", version: 12, status: "storyboard_preparing" },
  episode: { id: "episode-1", title: "第一集" },
  permissions: ["shot.view", "shot.manage"],
  featureFlags: {},
};

function shot(overrides: Partial<ShotItem> = {}): ShotItem {
  return {
    id: "shot-a",
    storyboardId: "storyboard-1",
    episodeId: "episode-1",
    shotNo: "01",
    sequenceNo: 10,
    version: 3,
    status: "draft",
    shotSize: "中景",
    cameraMove: "轻推",
    durationMs: 2400,
    dialogue: "",
    prompt: "",
    assetReferences: [],
    generationStatus: "draft",
    estimatedCredit: 8,
    issueCount: 0,
    qualityIssues: [],
    candidates: [],
    versions: [{ id: "shot-version-a1", versionNo: 1, shotSize: "中景", cameraMove: "轻推", durationMs: 2400, status: "succeeded", createdAt: "2026-07-16T00:00:00Z" }],
    updatedAt: "2026-07-16T00:00:00Z",
    ...overrides,
  };
}

function port(overrides: Partial<AssetsStoryboardPort> = {}): AssetsStoryboardPort {
  return {
    getSessionContext: vi.fn().mockResolvedValue(context),
    listAssets: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    listMarketAssets: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    listShots: vi.fn().mockResolvedValue({ items: [], nextToken: null, collectionVersion: 12 }),
    assetAction: vi.fn().mockResolvedValue({}),
    shotAction: vi.fn().mockResolvedValue({}),
    uploadImportFile: vi.fn().mockResolvedValue({ uploadId: "upload-1", sha256: "abc", fileName: "shots.csv" }),
    ...overrides,
  };
}

describe("分镜与故事板工作区", () => {
  it("服务端 403 时不泄露项目、剧集或镜头数据", async () => {
    const forbidden = new AssetsStoryboardApiError(403, "PERMISSION_DENIED", "无权读取镜头", "request-shot-denied");
    render(<StoryboardWorkspace scope={scope} projectName="机密项目" api={port({ listShots: vi.fn().mockRejectedValue(forbidden) })} view="list" />);

    expect(await screen.findByText("没有分镜查看权限")).toBeInTheDocument();
    expect(screen.queryByText("机密项目")).not.toBeInTheDocument();
    expect(screen.getByText(/request-shot-denied/)).toBeInTheDocument();
  });

  it("键盘替代重排提交稳定镜头 ID 数组，不以镜号作为身份", async () => {
    const action = vi.fn().mockResolvedValue({ shots: [] });
    const api = port({
      listShots: vi.fn().mockResolvedValue({ items: [shot(), shot({ id: "shot-b", shotNo: "02", sequenceNo: 20 })], nextToken: null, collectionVersion: 12 }),
      shotAction: action,
    });
    render(<StoryboardWorkspace scope={scope} api={api} view="list" />);
    const user = userEvent.setup();

    expect(await screen.findByText("shot-a")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "上移镜头 02" }));

    expect(action).toHaveBeenCalledWith(scope, expect.objectContaining({
      action: "reorder",
      payload: { orderedShotIds: ["shot-b", "shot-a"], baseVersion: 12 },
    }));
  });

  it("批量编辑逐项显示部分失败并只重试可恢复镜头", async () => {
    const action = vi.fn()
      .mockResolvedValueOnce({ batch: { operationId: "shot-batch-1", succeeded: 1, failed: 1, items: [
        { id: "shot-a", status: "succeeded" },
        { id: "shot-b", status: "failed", message: "资产引用已失效", retryable: true },
      ] } })
      .mockResolvedValueOnce({ batch: { operationId: "shot-batch-2", succeeded: 1, failed: 0, items: [{ id: "shot-b", status: "succeeded" }] } });
    const api = port({
      listAssets: vi.fn().mockResolvedValue({
        items: [{
          id: "asset-character-1", type: "character", name: "林导", tags: [], source: "project",
          status: "draft", rightsStatus: "verified", currentVersionId: "asset-character-v3",
          version: 3, referenceCount: 0, updatedAt: "2026-07-16T00:00:00Z", metadata: {}, versions: [],
        }],
        nextToken: null,
      }),
      listShots: vi.fn().mockResolvedValue({ items: [shot(), shot({ id: "shot-b", shotNo: "02" })], nextToken: null, collectionVersion: 12 }),
      shotAction: action,
    });
    render(<StoryboardWorkspace scope={scope} api={api} view="batch" />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "选择镜头 01" }));
    await user.click(screen.getByRole("checkbox", { name: "选择镜头 02" }));
    await user.type(screen.getByLabelText("景别"), "近景");
    await user.click(screen.getByRole("checkbox", { name: "绑定资产 林导" }));
    await user.click(screen.getByRole("button", { name: "应用批量编辑" }));
    expect(await screen.findByText("1 项成功，1 项失败")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅重试 1 个失败项" }));

    expect(action).toHaveBeenLastCalledWith(scope, expect.objectContaining({
      action: "retryFailedItems",
      payload: { itemIds: ["shot-b"], previousOperationId: "shot-batch-1" },
    }));
    expect(action).toHaveBeenNthCalledWith(1, scope, expect.objectContaining({
      payload: expect.objectContaining({
        changes: expect.objectContaining({
          assetReferences: [{ id: "asset-character-1", versionId: "asset-character-v3", type: "character" }],
        }),
      }),
    }));
  });

  it("导入先走真实上传和服务端预检，确认后才提交", async () => {
    const action = vi.fn()
      .mockResolvedValueOnce({ batch: { operationId: "preflight-1", succeeded: 1, failed: 0, items: [{ id: "row-1", status: "succeeded", message: "可导入" }] } })
      .mockResolvedValueOnce({ task: { taskId: "import-task-1", status: "queued" } });
    const uploadImportFile = vi.fn().mockResolvedValue({ uploadId: "upload-1", sha256: "abc", fileName: "shots.csv" });
    const api = port({ shotAction: action, uploadImportFile });
    render(<StoryboardWorkspace scope={scope} api={api} view="import" />);
    const user = userEvent.setup();
    const file = new File(["shotNo,durationMs\n01,2000"], "shots.csv", { type: "text/csv" });

    await user.upload(await screen.findByLabelText("选择分镜表文件"), file);
    await user.click(screen.getByRole("button", { name: "上传并预检" }));
    expect(uploadImportFile).toHaveBeenCalledWith(scope, file);
    expect(await screen.findByText("预检通过，可确认导入")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认导入" }));
    expect(screen.getByRole("dialog", { name: "确认导入分镜表" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并创建新版本" }));
    await waitFor(() => expect(action).toHaveBeenLastCalledWith(scope, expect.objectContaining({ action: "commitImport", payload: expect.objectContaining({ uploadId: "upload-1" }) })));
  });

  it("冻结故事板前显示版本与影响范围确认，成功后保留结果摘要", async () => {
    const action = vi.fn().mockResolvedValue({ task: { taskId: "freeze-task-1", status: "queued" }, message: "故事板冻结任务已创建" });
    const api = port({ listShots: vi.fn().mockResolvedValue({ items: [shot()], nextToken: null, collectionVersion: 12 }), shotAction: action });
    render(<StoryboardWorkspace scope={scope} api={api} view="storyboard" />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "冻结并确认故事板" }));
    expect(screen.getByRole("dialog", { name: "确认冻结故事板" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认冻结版本 12" }));

    expect(action).toHaveBeenCalledWith(scope, expect.objectContaining({ action: "confirmStoryboard", payload: { baseVersion: 12, shotIds: ["shot-a"], storyboardId: "storyboard-1" } }));
    expect(await screen.findByText("故事板冻结任务已创建")).toBeInTheDocument();
  });

  it("版本对比展示稳定镜头 ID，并通过确认动作恢复历史版本", async () => {
    const versioned = shot({ versions: [
      { id: "shot-version-a1", versionNo: 1, shotSize: "中景", cameraMove: "轻推", durationMs: 2400, status: "succeeded", createdAt: "2026-07-15T00:00:00Z" },
      { id: "shot-version-a2", versionNo: 2, shotSize: "特写", cameraMove: "固定", durationMs: 1800, status: "succeeded", createdAt: "2026-07-16T00:00:00Z" },
    ] });
    const action = vi.fn().mockResolvedValue({ shot: versioned });
    render(<StoryboardWorkspace scope={scope} api={port({ listShots: vi.fn().mockResolvedValue({ items: [versioned], nextToken: null, collectionVersion: 12 }), shotAction: action })} view="compare" />);
    const user = userEvent.setup();

    expect(await screen.findByText("shot-a")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "恢复 v1" }));
    expect(screen.getByRole("dialog", { name: "确认恢复镜头版本" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认恢复 v1" }));
    expect(action).toHaveBeenCalledWith(scope, expect.objectContaining({ action: "restoreVersion", targetId: "shot-a", version: 3, payload: { versionId: "shot-version-a1" } }));
  });
});
