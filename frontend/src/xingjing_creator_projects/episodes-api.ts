import i18n from "@/i18n";
import { CreatorApiError } from "./api";

export interface EpisodeProduction { scriptVersionId: string; assetSnapshotId: string; finalVideoVersionId: string }
export const emptyProduction = (): EpisodeProduction => ({ scriptVersionId: "", assetSnapshotId: "", finalVideoVersionId: "" });
export interface ProductionCatalog {
  revision: string;
  permissions: string[];
  scripts: { id: string; title: string; version: number }[];
  snapshots: { id: string; scriptVersionId: string; valid: boolean; assets: { id: string; versionId: string }[] }[];
  finals: { id: string; episodeId: string; finalVideoId: string; createdAt: string }[];
  items: { episode: number; state: string; issue: string | null; boards: { id: string; version: number; shots: number }[]; deliveries: { id: string; versionId: string; target: string; format: string; status: string; createdAt: string }[] }[];
}
export interface ProductionCommand {
  episode: number; title: string; endpoint: "shots/actions" | "exports"; payload: Record<string, unknown>;
  idempotencyKey: string; issue: string | null; existingId: string | null;
}
export interface EpisodeDraft {
  episode: number;
  title: string;
  hook: string;
  storyBeats: string[];
  nextEpisodeTeaser: string;
  targetDuration: number;
  budgetMinor: number | null;
  sourceRange: { source_file: string; start: number; end: number } | null;
  production?: EpisodeProduction;
}
export interface Episode extends EpisodeDraft {
  locked: boolean;
  status: string;
  scriptStatus: string;
  shots: number;
  storyboards: { total: number; completed: number };
  videos: { total: number; completed: number };
  issues: string[];
}
export interface EpisodesSnapshot {
  revision: string;
  items: Episode[];
  sources: { path: string; text: string }[];
  contentMode: string;
  generationMode: string;
  plannedCount: number;
  defaultDuration: number;
  nextEpisode: number;
  projectName?: string;
  total?: number;
}
export type EpisodeCosts = { episodes: { episode: number; totals: { estimate: Record<string, Record<string, number>>; actual: Record<string, Record<string, number>> } }[] };
export function episodeDraft(item: EpisodeDraft): EpisodeDraft {
  const { episode, title, hook, storyBeats, nextEpisodeTeaser, targetDuration, budgetMinor, sourceRange, production } = item;
  return { episode, title, hook, storyBeats: storyBeats.filter((beat) => beat.trim()), nextEpisodeTeaser, targetDuration, budgetMinor, sourceRange, production: production ?? emptyProduction() };
}
export function plannedEpisode(item: EpisodeDraft): Episode {
  return { ...item, locked: false, status: "draft", scriptStatus: "none", shots: 0, storyboards: { total: 0, completed: 0 }, videos: { total: 0, completed: 0 }, issues: [] };
}
export function createEpisodesApi(getAccessToken: () => string | null) {
  const keys = new Map<string, string>();
  async function request<T>(projectId: string, suffix = "", init: RequestInit = {}, resource = "episodes") {
    const token = getAccessToken();
    const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/${resource}${suffix}`, {
      ...init, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept-Language": i18n.resolvedLanguage ?? "zh", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
    });
    const body = await response.json().catch(() => ({})) as { data: T; error?: { code?: string; message?: string }; meta?: { requestId?: string } };
    if (!response.ok) throw new CreatorApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.error?.message ?? response.statusText, body.meta?.requestId, response.status >= 500);
    return body.data;
  }
  return {
    list: (id: string) => request<EpisodesSnapshot>(id, "?limit=500"),
    preview: (id: string, revision: string, source: string, count: number) => request<EpisodeDraft[]>(id, "/split-preview", { method: "POST", headers: { "If-Match": `"${revision}"` }, body: JSON.stringify({ source, count }) }),
    costs: (id: string) => request<EpisodeCosts>(id, "/costs"),
    production: (id: string) => request<ProductionCatalog>(id, "/production"),
    prepare: (id: string, revision: string, episodes: number[], action: "storyboards" | "packages", target: string) => request<ProductionCommand[]>(id, "/production/prepare", { method: "POST", headers: { "If-Match": `"${revision}"` }, body: JSON.stringify({ episodes, action, target }) }),
    execute: (id: string, command: ProductionCommand) => request<{ delivery?: { request_id: string; status: string }; shots?: unknown[] }>(id, "", { method: "POST", headers: { "Idempotency-Key": command.idempotencyKey }, body: JSON.stringify(command.payload) }, command.endpoint),
    save: async (id: string, revision: string, items: Episode[]) => {
      const body = JSON.stringify({ items: items.map(episodeDraft) });
      const operation = `${id}:${revision}:${body}`;
      const key = keys.get(operation) ?? crypto.randomUUID();
      keys.set(operation, key);
      try {
        const result = await request<EpisodesSnapshot>(id, "", { method: "PUT", body, headers: { "If-Match": `"${revision}"`, "Idempotency-Key": key } });
        keys.delete(operation);
        return result;
      } catch (error) {
        if (error instanceof CreatorApiError && !error.retryable) keys.delete(operation);
        throw error;
      }
    },
  };
}
export type EpisodesApi = ReturnType<typeof createEpisodesApi>;
