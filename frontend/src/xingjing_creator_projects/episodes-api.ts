import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";
import { CreatorApiError } from "./api";

export interface EpisodeProduction { scriptVersionId: string; assetSnapshotId: string; finalVideoVersionId: string }
export interface EpisodeSource { path: string; label: string; kind: "file" | "sql_original"; scriptId: string | null; digest: string; length: number; coordinateSystem: "nfc_lf" }
export interface EpisodeSourceText extends EpisodeSource { text: string }
export function episodeSourceLabel(source: EpisodeSource, sources: EpisodeSource[]) {
  const duplicates = sources.filter((item) => item.label === source.label);
  if (duplicates.length < 2) return source.label;
  const identity = source.scriptId ?? source.path;
  const short = identity.slice(-8);
  const ambiguous = duplicates.some((item) => (item.scriptId ?? item.path) !== identity && (item.scriptId ?? item.path).slice(-8) === short);
  return `${source.label} · ${ambiguous ? identity : short}`;
}
export interface WorkingScript {
  scriptId: string; revision: number; versionNumber: number; lockedVersionNumber: number | null;
  validationState: "valid" | "invalid"; rangeMatches: boolean; createdAt: string;
  sourceRef: { kind: "file" | "sql_original"; sourceId: string; sourceScriptId: string | null; digest: string; coordinateSystem: "nfc_lf"; start: number; end: number };
}
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
  workingScript?: WorkingScript | null;
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
  sources: EpisodeSource[];
  permissions?: { canReadSources: boolean; canCreateWorkingScript: boolean };
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
  const fetcher = createAuthenticatedFetcher();
  const keys = new Map<string, string>();
  async function request<T>(projectId: string, suffix = "", init: RequestInit = {}, resource = "episodes") {
    const token = getAccessToken();
    const response = await fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/${resource}${suffix}`, {
      ...init, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept-Language": i18n.resolvedLanguage ?? "zh", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
    });
    const body = await response.json().catch(() => ({})) as { data: T; error?: { code?: string; message?: string }; meta?: { requestId?: string } };
    if (!response.ok) throw new CreatorApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.error?.message ?? response.statusText, body.meta?.requestId, response.status >= 500);
    return body.data;
  }
  return {
    list: (id: string) => request<EpisodesSnapshot>(id, "?limit=500"),
    source: (id: string, sourceId: string, signal?: AbortSignal) => request<{ source: EpisodeSourceText }>(id, "/source?" + new URLSearchParams({ sourceId }).toString(), { signal }).then((data) => data.source),
    preview: (id: string, revision: string, source: string, count: number, sourceDigest: string) => request<EpisodeDraft[]>(id, "/split-preview", { method: "POST", headers: { "If-Match": `"${revision}"` }, body: JSON.stringify({ source, count, sourceDigest }) }),
    createWorkingScript: async (id: string, episode: number, revision: string, sourceDigest: string, previousWorkingScriptId?: string) => {
      const body = JSON.stringify({ sourceDigest, ...(previousWorkingScriptId ? { previousWorkingScriptId } : {}) });
      const operation = `working:${id}:${episode}:${revision}:${body}`;
      const key = keys.get(operation) ?? crypto.randomUUID();
      keys.set(operation, key);
      try {
        const result = await request<{ workingScript: WorkingScript; revision: string; created: boolean }>(id, `/${episode}/working-script`, { method: "POST", body, headers: { "If-Match": `"${revision}"`, "Idempotency-Key": key } });
        keys.delete(operation);
        return result;
      } catch (error) {
        if (error instanceof CreatorApiError && !error.retryable && error.status !== 401) keys.delete(operation);
        throw error;
      }
    },
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
