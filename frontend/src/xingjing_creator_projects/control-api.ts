import i18n from "@/i18n";
import { CreatorApiError } from "./api";
import type { ProjectDetail, ProjectSetup } from "./types";

export interface ControlEpisode {
  episode: number; title: string; state: string; issue: string | null; shots: number | null;
  finalVersionId: string | null; timelineId: string | null; approval: string | null;
  assetsReady: boolean;
}
export interface ProjectControl {
  project: Omit<ProjectDetail, "configuration"> & { configuration?: Partial<ProjectSetup> }; workspaceId: string; permissions: string[]; plannedCount: number; updatedAt: string; days: number;
  episodes: ControlEpisode[];
  episodePage: { items: ControlEpisode[]; total: number; offset: number; limit: number };
  stages: { id: string; target: string; reason: string | null; recovery: string | null }[];
  tasks: { counts: Record<string, number>; items: { id: string; status: string; mediaType: string; updatedAt: string }[] } | null;
  costs: { currency: string; spentMinor: number; heldMinor: number }[] | null;
  transitions: { action: "advance" | "rollback"; target: string; reason: string | null }[];
  history: { id: string; actorId: string; action: string; from: string; to: string; reason: string; createdAt: string; requestId: string }[];
}
export interface ControlFilters { days?: number; q?: string; episodeState?: string; offset?: number }
export function createProjectControlApi(getToken: () => string | null) {
  return {
    async get(projectId: string, filters: ControlFilters = {}, signal?: AbortSignal): Promise<ProjectControl> {
      const query = new URLSearchParams(Object.entries(filters).map(([k, v]) => [k, String(v)]));
      const token = getToken();
      const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/control?${query}`, {
        credentials: "same-origin", signal,
        headers: { Accept: "application/json", "Accept-Language": i18n.resolvedLanguage ?? "zh", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      });
      const body = await response.json() as { data: ProjectControl; error?: { code?: string; message?: string }; meta?: { requestId?: string } };
      if (!response.ok) throw new CreatorApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.error?.message ?? response.statusText, body.meta?.requestId, response.status >= 500);
      return body.data;
    },
  };
}
export type ProjectControlApi = ReturnType<typeof createProjectControlApi>;
