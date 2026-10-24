import { createAuthenticatedFetcher } from "@/utils/auth";
import i18n from "@/i18n";

export interface ReviewShot {
  id: string;
  clipId: string;
  startMs: number;
  endMs: number;
}

export interface FinalVideo {
  id: string;
  finalVideoId: string;
  timelineId: string;
  timelineVersionId: string;
  episodeId: string;
  createdAt: string;
  durationMs: number;
  revision: number;
  shots: ReviewShot[];
}

export interface ReviewRound {
  id: string;
  number: number;
  title: string;
  note: string;
  finalVideoVersionId: string;
  state: "collecting" | "approved" | "changes_requested";
  locked: boolean;
  version: number;
  createdAt: string;
  updatedAt: string;
  decisionNote: string | null;
  decidedBy: string | null;
  decidedAt: string | null;
  lockedAt: string | null;
  openComments: number;
  totalComments: number;
}

export interface InternalComment {
  id: string;
  roundId: string;
  finalVideoVersionId: string;
  authorId: string;
  timecodeMs: number;
  shotId: string | null;
  body: string;
  severity: string;
  parentCommentId: string | null;
  status: "open" | "in_progress" | "resolved" | "reopened";
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface InternalPermissions {
  canManage: boolean;
  canComment: boolean;
  canDecide: boolean;
  canLock: boolean;
  canHandoff: boolean;
}

export interface RoundPage extends InternalPermissions {
  workspaceId: string;
  projectId: string;
  actorId: string;
  rounds: ReviewRound[];
  total: number;
  finalVideos: FinalVideo[];
}

export interface RoundDetail extends InternalPermissions {
  workspaceId: string;
  projectId: string;
  actorId: string;
  round: ReviewRound;
  finalVideo: FinalVideo;
  comments: InternalComment[];
  media: { available: boolean; url: string | null; expiresAt: string | null; reasonCode?: string };
}

export interface ReviewListOptions {
  q: string;
  state: string;
  offset: number;
  limit: number;
  finalVideoVersionId?: string;
  episodeId?: string;
}

export class InternalReviewError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) {
    super(code);
  }
}

export interface InternalReviewApi {
  list(options: ReviewListOptions, signal?: AbortSignal): Promise<RoundPage>;
  detail(roundId: string, signal?: AbortSignal): Promise<RoundDetail>;
  create(input: { finalVideoVersionId: string; title: string; note: string }): Promise<{ round: ReviewRound }>;
  comment(roundId: string, input: { version: number; timecodeMs: number; shotId?: string; body: string; severity: string; parentCommentId?: string }): Promise<{ round: ReviewRound; comment: InternalComment }>;
  status(roundId: string, commentId: string, input: { version: number; commentVersion: number; status: string }): Promise<{ round: ReviewRound; comment: InternalComment }>;
  decide(roundId: string, input: { version: number; decision: "approved" | "changes_requested"; note: string }): Promise<{ round: ReviewRound }>;
  lock(roundId: string, input: { version: number; note: string }): Promise<{ round: ReviewRound }>;
  reopen(roundId: string, input: { version: number; note: string }): Promise<{ round: ReviewRound }>;
  handoff(roundId: string, input: { version: number }): Promise<{ round: ReviewRound; navigation: { target: string; deliveryTarget: string; projectVersion: string; timelineId: string; episodeId: string } }>;
}

export function createInternalReviewApi(workspaceId: string, projectId: string, token?: string): InternalReviewApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId });
  const prefix = `/api/v1/projects/${encodeURIComponent(projectId)}/internal-reviews`;
  const retries = new Map<string, string>();

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Request-Id", crypto.randomUUID());
    headers.set("Accept-Language", i18n.resolvedLanguage ?? i18n.language);
    headers.set("X-Workspace-Id", workspaceId);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetcher(path, { ...init, headers });
    const value = await response.json().catch(() => null) as { data?: T; error?: { code?: string }; detail?: { code?: string }; meta?: { requestId?: string } } | null;
    if (!response.ok || !value || value.data === undefined) {
      throw new InternalReviewError(response.status, value?.error?.code ?? value?.detail?.code ?? "REVIEW_RUNTIME_UNAVAILABLE", value?.meta?.requestId);
    }
    return value.data;
  }

  async function mutate<T>(path: string, input: object): Promise<T> {
    const body = JSON.stringify(input);
    const identity = `${path}:${body}`;
    const key = retries.get(identity) ?? crypto.randomUUID();
    retries.set(identity, key);
    const result = await request<T>(path, { method: "POST", headers: { "Idempotency-Key": key }, body });
    retries.delete(identity);
    return result;
  }

  return {
    list: (options, signal) => {
      const query = new URLSearchParams({ q: options.q, state: options.state, offset: String(options.offset), limit: String(options.limit) });
      if (options.finalVideoVersionId) query.set("finalVideoVersionId", options.finalVideoVersionId);
      if (options.episodeId) query.set("episodeId", options.episodeId);
      return request(`${prefix}?${query}`, { signal });
    },
    detail: (roundId, signal) => request(`${prefix}/${encodeURIComponent(roundId)}`, { signal }),
    create: input => mutate(prefix, input),
    comment: (roundId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/comments`, input),
    status: (roundId, commentId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/comments/${encodeURIComponent(commentId)}/status`, input),
    decide: (roundId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/decision`, input),
    lock: (roundId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/lock`, input),
    reopen: (roundId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/reopen`, input),
    handoff: (roundId, input) => mutate(`${prefix}/${encodeURIComponent(roundId)}/handoff`, input),
  };
}
