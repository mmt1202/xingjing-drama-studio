import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectLibraryPage, ScriptImportPage, type CreatorProjectsPort } from ".";

const context = { user: { id: "u1", name: "林导" }, workspace: { id: "w1", name: "第一工作室" }, permissions: ["project.view", "project.manage", "script.view", "script.manage"], featureFlags: {} };

function port(overrides: Partial<CreatorProjectsPort> = {}): CreatorProjectsPort {
  return {
    getSessionContext: vi.fn().mockResolvedValue(context),
    listProjects: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    getProject: vi.fn(),
    projectAction: vi.fn(),
    listScripts: vi.fn().mockResolvedValue({ items: [], nextToken: null }),
    scriptAction: vi.fn(),
    ...overrides,
  };
}

describe("项目库", () => {
  it("区分加载、空数据，并提供真实创建入口回调", async () => {
    const onNavigate = vi.fn();
    render(<ProjectLibraryPage api={port()} onNavigate={onNavigate} />);
    expect(screen.getByText("正在恢复工作区…")).toBeInTheDocument();
    expect(await screen.findByText("这个工作区还没有项目")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    expect(onNavigate).toHaveBeenCalledWith("create");
  });

  it("权限拒绝时不泄露项目数据", async () => {
    render(<ProjectLibraryPage api={port({ getSessionContext: vi.fn().mockResolvedValue({ ...context, permissions: [] }) })} onNavigate={vi.fn()} />);
    expect(await screen.findByText("没有项目查看权限")).toBeInTheDocument();
    expect(screen.queryByText("林导")).not.toBeInTheDocument();
  });

  it("归档后重读服务端状态，重复点击不会重复提交", async () => {
    const action = vi.fn().mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({ id: "p1", version: 3 }), 10)));
    const list = vi.fn()
      .mockResolvedValueOnce({ items: [{ id: "p1", name: "山海", type: "series", targetPlatform: "抖音", ownerName: "林导", productionStatus: "draft", archived: false, version: 2, updatedAt: "2026-07-15T00:00:00Z" }], nextToken: null })
      .mockResolvedValueOnce({ items: [], nextToken: null });
    render(<ProjectLibraryPage api={port({ listProjects: list, projectAction: action })} onNavigate={vi.fn()} />);
    const archive = await screen.findByRole("button", { name: "归档山海" });
    fireEvent.click(archive);
    fireEvent.click(archive);
    await waitFor(() => expect(action).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });
});

describe("剧本导入", () => {
  it("异步解析显示处理中，失败后可用同一动作恢复", async () => {
    const scriptAction = vi.fn()
      .mockResolvedValueOnce({ taskId: "t1", status: "queued", statusUrl: "/api/v1/tasks/t1" })
      .mockRejectedValueOnce(Object.assign(new Error("模型暂不可用"), { requestId: "req-2" }))
      .mockResolvedValueOnce({ taskId: "t2", status: "queued", statusUrl: "/api/v1/tasks/t2" });
    render(<ScriptImportPage api={port({ scriptAction })} projectId="p1" />);
    await screen.findByText("尚未导入原文或剧本");
    fireEvent.change(screen.getByLabelText("选择源文件"), { target: { files: [new File(["正文"], "故事.txt", { type: "text/plain" })] } });
    fireEvent.click(screen.getByRole("button", { name: "导入并解析" }));
    expect(await screen.findByText("解析任务已进入队列")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新解析" }));
    expect(await screen.findByText("模型暂不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(scriptAction).toHaveBeenCalledTimes(3));
  });
});
