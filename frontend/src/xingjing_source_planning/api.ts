import i18n from "@/i18n";
import { createAuthenticatedFetcher, getSessionEpoch, getTokenForSession } from "@/utils/auth";
import { CreatorApiError } from "@/xingjing_creator_projects/api";
import type { EpisodeSource } from "@/xingjing_creator_projects/episodes-api";
import type { MappingCommand, MappingResult, MappingView } from "./mapping-types";

export interface Chapter { id: string; ordinal: number; title: string | null; kind: "heading" | "preamble" | "body"; start: number; end: number }
export interface SourceReference { sourceId: string; digest: string; start: number; end: number; snapshotId?: string; chapterIds?: string[]; mappingKind?: string; planId?: string }
export interface Candidate { id: string; title: string; start: number; end: number; targetDuration: number }
export interface ProposedCandidate extends Candidate { episode: number; chapterIds: string[]; excerpt: string }
export interface CreatePlan { sourceId: string; sourceDigest: string; episodeRevision: string; count: number; chapterIds: string[] }
export interface ConfirmPlan { candidates: Candidate[] }
export interface PlanResult { planId: string; snapshotId: string; episodeRevision: string; confirmedAt: string; newProviderCalls: number; items: { episode: number; scriptId: string; title: string; sourceRef: SourceReference }[] }
export type PendingOperation = { kind: "create"; key: string; payload: CreatePlan } | { kind: "confirm"; key: string; planId: string; payload: ConfirmPlan };
export interface Plan {
  id: string; projectId: string; snapshotId: string; source: EpisodeSource; chapters: Chapter[]; episodeRevision: string;
  proposal: { method: "chapter_paragraph_rules"; sourceLabel: string; candidates: ProposedCandidate[]; availableRanges: [number, number][]; uncoveredCharacters: number };
  result: PlanResult | null; createdAt: string; canManage: boolean; confirmationPending: boolean;
  pendingConfirmation: { key: string; payload: ConfirmPlan } | null;
}
export interface Catalog { source: EpisodeSource; snapshotId: string; chapters: Chapter[]; episodeRevision: string; sourceChanged: boolean; uncoveredRanges: [number, number][]; uncoveredCharacters: number; coordinateSystem: string }
export interface PlanHistory { items: { id: string; sourceLabel: string; count: number; createdAt: string; applied: boolean; confirmationPending?: boolean }[]; canManage: boolean }
export interface SnapshotExcerpt { snapshotId: string; source: EpisodeSource; start: number; end: number; text: string; chapters: Chapter[]; coordinateSystem: string }

export function createSourcePlanApi(workspaceId: string) {
  const session = getSessionEpoch(), fetcher = createAuthenticatedFetcher();
  async function request<T>(projectId: string, path: string, init: RequestInit = {}): Promise<T> {
    const token = getTokenForSession(session);
    const response = await fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/${path}`, {
      ...init, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept-Language": i18n.resolvedLanguage ?? "zh", "X-Workspace-Id": workspaceId,
        ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
    });
    const body = await response.json().catch(() => ({})) as { data: T; error?: { code?: string; message?: string }; meta?: { requestId?: string } };
    if (!response.ok) throw new CreatorApiError(response.status, body.error?.code ?? "REQUEST_FAILED", body.error?.message ?? response.statusText, body.meta?.requestId, response.status >= 500 || response.status === 408 || response.status === 429);
    return body.data;
  }
  return {
    mapping: (id: string, scriptId: string, version: number, offset?: number, find?: string, signal?: AbortSignal) => request<MappingView>(id, `scripts/${encodeURIComponent(scriptId)}/provenance-mapping?${new URLSearchParams({ version: String(version), ...(offset !== undefined ? { offset: String(offset) } : {}), ...(find ? { find } : {}) })}`, { signal }),
    confirmMapping: (id: string, scriptId: string, payload: MappingCommand, key: string) => request<MappingResult>(id, `scripts/${encodeURIComponent(scriptId)}/provenance-mapping`, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) }),
    list: (id: string, signal?: AbortSignal) => request<PlanHistory>(id, "episode-plans", { signal }),
    catalog: (id: string, sourceId: string, signal?: AbortSignal) => request<Catalog>(id, "episode-plans/source?" + new URLSearchParams({ sourceId }).toString(), { signal }),
    get: (id: string, planId: string, signal?: AbortSignal) => request<Plan>(id, `episode-plans/${encodeURIComponent(planId)}`, { signal }),
    create: (id: string, payload: CreatePlan, key: string) => request<Plan>(id, "episode-plans", { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) }),
    confirm: (id: string, planId: string, payload: ConfirmPlan, key: string) => request<PlanResult>(id, `episode-plans/${encodeURIComponent(planId)}/confirm`, { method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(payload) }),
    snapshot: (id: string, snapshotId: string, start: number, end: number, signal?: AbortSignal) => request<SnapshotExcerpt>(id, `source-snapshots/${encodeURIComponent(snapshotId)}?${new URLSearchParams({ start: String(start), end: String(end) })}`, { signal }),
    provenance: (id: string, scriptId: string, signal?: AbortSignal) => request<{ sourceRef: SourceReference | null }>(id, `scripts/${encodeURIComponent(scriptId)}/source-provenance`, { signal }),
  };
}
export type SourcePlanApi = ReturnType<typeof createSourcePlanApi>;
export const candidateInput = ({ id, title, start, end, targetDuration }: Candidate): Candidate => ({ id, title, start, end, targetDuration });
export const denied = (error: unknown) => error instanceof CreatorApiError && [401, 403].includes(error.status);
export const uncertain = (error: unknown) => !(error instanceof CreatorApiError) || error.retryable || error.status === 401;
