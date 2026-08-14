import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { XingjingApiError } from "./api";
import { GenerationAudioWorkspace } from "./GenerationAudioWorkspace";

function api(overrides: Record<string, unknown> = {}) {
  return {
    audioTracks: vi.fn().mockResolvedValue({ data: { audioTracks: [], subtitleTracks: [] }, meta: { total: 0 } }),
    audioTasks: vi.fn().mockResolvedValue({ data: { tasks: [{ id: "task-1", task_kind: "audio", resource_type: "audio_track", resource_id: "track-1", status: "queued", version: 1, estimated_minor: 80, billing_currency: "CNY", billing_status: "active" }] }, meta: { total: 1 } }),
    lipSyncVersions: vi.fn().mockResolvedValue({ data: { versions: [] }, meta: { total: 0 } }),
    trackVersions: vi.fn(),
    audioAction: vi.fn(),
    audioObjectUrl: vi.fn(),
    generatedAssetUrl: vi.fn(),
    ...overrides,
  };
}

describe("audio subtitle workspace", () => {
  it("loads project-scoped server tasks and billing state", async () => {
    render(<GenerationAudioWorkspace projectId="p1" projectName="第一集" api={api() as never} />);
    expect(screen.getByLabelText("音频字幕工作区")).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByText("项目 / 第一集 / CR-007")).toBeInTheDocument();
    expect(screen.getByText(/track-1 · 状态 queued/)).toBeInTheDocument();
    expect(screen.getByText(/预估 80 CNY/)).toBeInTheDocument();
  });

  it("does not reveal project data after a 403", async () => {
    const forbidden = new XingjingApiError({ status: 403, code: "PERMISSION_DENIED", message: "禁止" });
    render(<GenerationAudioWorkspace projectId="secret" projectName="机密项目" api={api({ audioTracks: vi.fn().mockRejectedValue(forbidden) }) as never} />);

    expect(await screen.findByText("无权访问音频字幕")).toBeInTheDocument();
    expect(screen.queryByText("机密项目")).not.toBeInTheDocument();
  });
});
