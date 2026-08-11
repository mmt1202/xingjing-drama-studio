import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AssetsStoryboardApiError } from "./api";
import { AssetsWorkspace } from "./AssetsWorkspace";
import type { AssetItem, AssetsStoryboardPort, SessionContext } from "./contracts";

const scope = { workspaceId: "workspace-1", projectId: "project-1" };
const context: SessionContext = {
  user: { id: "user-1", name: "林导" },
  workspace: { id: "workspace-1", name: "镜像工作室" },
  project: { id: "project-1", name: "雾港", version: 7, status: "asset_preparing" },
  permissions: ["asset.view", "asset.manage", "script.view", "script.manage"],
  featureFlags: {},
};

function asset(overrides: Partial<AssetItem> = {}): AssetItem {
  return {
    id: "asset-a",
    projectId: "project-1",
    type: "character",
    name: "角色甲",
    tags: ["主角"],
    source: "script-extraction",
    status: "draft",
    rightsStatus: "pending",
    currentVersionId: "asset-version-a1",
    version: 3,
    referenceCount: 4,
    updatedAt: "2026-07-16T00:00:00Z",
    description: "",
    metadata: {},
    versions: [{ id: "asset-version-a1", versionNo: 1, status: "succeeded", source: "script-extraction", createdAt: "2026-07-16T00:00:00Z" }],
    ...overrides,
  };
}

function port(overrides: Partial<AssetsStoryboardPort> = {}): AssetsStoryboardPort {
  return {
    getSessionContext: vi.fn().mockResolvedValue(context),
    listAssets: vi.fn().mockResolvedValue({ items: [], nextToken: null, collectionVersion: 7 }),
    listMarketAssets: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    listShots: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    assetAction: vi.fn().mockResolvedValue({}),
    shotAction: vi.fn().mockResolvedValue({}),
    uploadImportFile: vi.fn(),
    ...overrides,
  };
}

describe("主体与素材资产工作区", () => {
  it("恢复服务端上下文后区分结构化加载态与可操作空态", async () => {
    render(<AssetsWorkspace scope={scope} api={port()} view="library" />);

    expect(screen.getByText("正在恢复工作区与项目上下文…")).toBeInTheDocument();
    expect(await screen.findByText("当前范围没有资产")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提取剧本资产" })).toBeInTheDocument();
  });

  it("服务端拒绝时清空敏感对象并展示申请权限动作", async () => {
    const forbidden = new AssetsStoryboardApiError(403, "PERMISSION_DENIED", "无权读取资产", "request-denied");
    render(<AssetsWorkspace scope={scope} projectName="机密项目" api={port({ listAssets: vi.fn().mockRejectedValue(forbidden) })} view="library" />);

    expect(await screen.findByText("没有资产查看权限")).toBeInTheDocument();
    expect(screen.queryByText("机密项目")).not.toBeInTheDocument();
    expect(screen.getByText(/request-denied/)).toBeInTheDocument();
  });

  it("批处理逐项展示成功失败，安全重试只提交失败资产 ID", async () => {
    const action = vi.fn()
      .mockResolvedValueOnce({ batch: { operationId: "batch-1", succeeded: 1, failed: 1, items: [
        { id: "asset-a", status: "succeeded" },
        { id: "asset-b", status: "failed", code: "RIGHTS_MISSING", message: "缺少权利证明", retryable: true },
      ] } })
      .mockResolvedValueOnce({ batch: { operationId: "batch-2", succeeded: 1, failed: 0, items: [{ id: "asset-b", status: "succeeded" }] } });
    const api = port({
      listAssets: vi.fn().mockResolvedValue({ items: [asset(), asset({ id: "asset-b", type: "scene", name: "场景乙" })], nextToken: null, collectionVersion: 7 }),
      assetAction: action,
    });
    render(<AssetsWorkspace scope={scope} api={api} view="library" />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "选择 角色甲" }));
    await user.click(screen.getByRole("checkbox", { name: "选择 场景乙" }));
    await user.click(screen.getByRole("button", { name: "批量确认授权" }));

    expect(await screen.findByText("1 项成功，1 项失败")).toBeInTheDocument();
    expect(screen.getByText("缺少权利证明")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "仅重试 1 个失败项" }));
    expect(action).toHaveBeenLastCalledWith(scope, expect.objectContaining({
      action: "retryFailedItems",
      payload: { itemIds: ["asset-b"], previousOperationId: "batch-1" },
    }));
  });

  it("编辑冲突不静默覆盖，可重新读取或另存新版本", async () => {
    const action = vi.fn()
      .mockRejectedValueOnce(new AssetsStoryboardApiError(409, "VERSION_CONFLICT", "对象已被其他成员更新", "request-conflict", false, [{ field: "version", reason: "expected 3, actual 4" }]))
      .mockResolvedValueOnce({ asset: asset({ version: 4 }) });
    const api = port({ listAssets: vi.fn().mockResolvedValue({ items: [asset()], nextToken: null, collectionVersion: 7 }), assetAction: action });
    render(<AssetsWorkspace scope={scope} assetId="asset-a" api={api} view="editor" />);
    const user = userEvent.setup();

    const name = await screen.findByLabelText("资产名称");
    await user.clear(name);
    await user.type(name, "角色甲新版");
    await user.click(screen.getByRole("button", { name: "保存资产" }));

    expect(await screen.findByText("版本冲突")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取服务端版本" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "另存为新版本" }));
    expect(action).toHaveBeenLastCalledWith(scope, expect.objectContaining({ action: "createVersion", targetId: "asset-a" }));
  });

  it("资产提取动作绑定剧本版本并进入可追踪处理态", async () => {
    const action = vi.fn().mockResolvedValue({ task: { taskId: "task-extract-1", status: "queued", progress: 0, statusUrl: "/api/v1/generation-tasks/task-extract-1" } });
    render(<AssetsWorkspace scope={scope} api={port({ assetAction: action })} view="extract" />);
    const user = userEvent.setup();

    await user.type(await screen.findByLabelText("冻结剧本版本 ID"), "script-version-9");
    await user.click(screen.getByRole("button", { name: "开始提取资产" }));

    expect(action).toHaveBeenCalledWith(scope, expect.objectContaining({ action: "extractAssets", payload: expect.objectContaining({ scriptVersionId: "script-version-9" }) }));
    expect(await screen.findByText("提取任务已进入队列")).toBeInTheDocument();
  });

  it("为五类主体提供各自的业务编辑字段", async () => {
    const cases: Array<[AssetItem["type"], string]> = [
      ["character", "角色外观"],
      ["scene", "场景光线"],
      ["prop", "道具材质"],
      ["costume", "服装设定"],
      ["voice", "声音语言"],
    ];

    for (const [type, label] of cases) {
      const current = asset({ id: `asset-${type}`, type, name: type });
      const view = render(<AssetsWorkspace scope={scope} assetId={current.id} api={port({ listAssets: vi.fn().mockResolvedValue({ items: [current], nextToken: null }) })} view="editor" />);
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
      view.unmount();
    }
  });

  it("设为主版本必须经过明确影响确认", async () => {
    const action = vi.fn().mockResolvedValue({ asset: asset() });
    render(<AssetsWorkspace scope={scope} assetId="asset-a" api={port({ listAssets: vi.fn().mockResolvedValue({ items: [asset()], nextToken: null }), assetAction: action })} view="editor" />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "设 v1 为主版本" }));
    expect(screen.getByRole("dialog", { name: "确认设为主版本" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认设为主版本" }));
    await waitFor(() => expect(action).toHaveBeenCalledWith(scope, expect.objectContaining({ action: "setPrimaryVersion", targetId: "asset-a", version: 3 })));
  });

  it("市场素材须在确认授权后才加入工作区资产库", async () => {
    const action = vi.fn().mockResolvedValue({ asset: asset() });
    render(<AssetsWorkspace scope={{ workspaceId: "workspace-1" }} assetId="asset-a" api={port({ listMarketAssets: vi.fn().mockResolvedValue({ items: [asset()], nextToken: null }), assetAction: action })} view="market-detail" />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "确认授权并加入资产库" }));
    expect(screen.getByRole("dialog", { name: "确认授权并加入资产库" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认加入资产库" }));
    await waitFor(() => expect(action).toHaveBeenCalledWith({ workspaceId: "workspace-1" }, expect.objectContaining({ action: "licenseMarketAsset", targetId: "asset-a" })));
  });
});
