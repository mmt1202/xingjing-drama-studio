import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";

export type ReviewPermission = "review.view" | "review.comment" | "review.approve" | "review.download";

export interface ReviewPolicy {
  comment: boolean;
  approve: boolean;
  download: boolean;
}

export interface ReviewDelivery {
  reviewLinkId: string;
  finalVideoVersionId: string;
  status: string;
  version: number;
  confirmedBy: string | null;
  confirmedAt: string | null;
}

export interface ReviewLink {
  id: string;
  workspaceId: string;
  projectId: string;
  finalVideoVersionId: string;
  policy: ReviewPolicy;
  watermarkText: string | null;
  expiresAt: string | null;
  state: string;
  version: number;
  createdAt: string;
  revokedAt: string | null;
  delivery: ReviewDelivery | null;
  token?: string;
  accessSecret?: string;
}

export interface ReviewComment {
  id: string;
  reviewLinkId: string;
  finalVideoVersionId: string;
  authorId: string;
  timecodeMs: number;
  body: string;
  severity: string;
  screenshotAssetId: string | null;
  parentCommentId: string | null;
  status: string;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface PublicReviewContext {
  review: ReviewLink;
  session: { id: string; permissions: ReviewPermission[]; ticket: string };
  media: { available: boolean; mediaType: string; filename: string; downloadAllowed: boolean };
  comments: ReviewComment[];
}

export interface ReviewPage { reviewLinks: ReviewLink[]; nextToken: string | null; canManage: boolean }
export interface ListOptions { query?: string; pageToken?: string; pageSize?: number; signal?: AbortSignal }
export type PublicReviewResult = PublicReviewContext | { verificationRequired: true };

export class ReviewApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) {
    super(code);
    this.name = "ReviewApiError";
  }
}

interface ApiEnvelope<T> { data: T; meta?: { requestId?: string } }
interface ApiErrorEnvelope { error?: { code?: string }; meta?: { requestId?: string } }

export interface ReviewApi {
  openPublicContext(token: string, accessSecret?: string, options?: { sessionId?: string; signal?: AbortSignal }): Promise<PublicReviewResult>;
  publicAction(token: string, sessionId: string, payload: Record<string, unknown>): Promise<{ comment?: ReviewComment; delivery?: ReviewDelivery }>;
  uploadScreenshot(token: string, sessionId: string, file: File, timecodeMs: number): Promise<{ id: string }>;
  logout(token: string, sessionId: string): Promise<void>;
  mediaUrl(token: string, sessionId: string, ticket: string, download?: boolean): string;
  screenshotUrl(token: string, sessionId: string, ticket: string, screenshotId: string): string;
  projectLinks(projectId: string, options?: ListOptions): Promise<ReviewPage>;
  workspaceLinks(workspaceId: string, options?: ListOptions): Promise<ReviewPage>;
  projectReviewDefaults?: (projectId: string, signal?: AbortSignal) => Promise<ReviewPolicy>;
  createProjectLink(projectId: string, input: CreateReviewLinkInput): Promise<ReviewLink>;
  createWorkspaceLink(workspaceId: string, input: CreateReviewLinkInput & { projectId: string }): Promise<ReviewLink>;
  revokeWorkspaceLink(workspaceId: string, linkId: string, version: number): Promise<ReviewLink>;
  reviewDetail(workspaceId: string, linkId: string, signal?: AbortSignal): Promise<{ reviewLink: ReviewLink; comments: ReviewComment[] }>;
  changeCommentStatus(workspaceId: string, commentId: string, status: string, version: number): Promise<ReviewComment>;
  replyToComment(workspaceId: string, commentId: string, body: string): Promise<ReviewComment>;
}

export interface CreateReviewLinkInput {
  finalVideoVersionId: string;
  expiresAt?: string;
  accessSecret?: string;
  watermarkText?: string;
  policy: ReviewPolicy;
}

function uniqueKey(): string {
  return crypto.randomUUID();
}

export function createReviewApi({ accessToken, baseUrl = "/api/v1", fetcher = fetch }: { accessToken?: string; baseUrl?: string; fetcher?: typeof fetch } = {}): ReviewApi {
  const authenticated = createAuthenticatedFetcher({ fetcher });
  const retries = new Map<string, string>();
  async function request<T>(path: string, init: RequestInit = {}, options: { publicRequest?: boolean; sessionId?: string; accessSecret?: string } = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("Accept-Language", i18n.resolvedLanguage ?? i18n.language);
    headers.set("X-Request-Id", uniqueKey());
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (!options.publicRequest && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    if (options.sessionId) headers.set("X-Review-Session-Id", options.sessionId);
    if (options.accessSecret) headers.set("X-Review-Access-Secret", options.accessSecret);

    let response: Response;
    try {
      response = await (options.publicRequest ? fetcher : authenticated)(`${baseUrl}${path}`, { ...init, headers, cache: "no-store", credentials: options.publicRequest ? "omit" : "same-origin", referrerPolicy: "no-referrer" });
    } catch (error) {
      if (init.signal?.aborted) throw error;
      throw new ReviewApiError(0, "NETWORK_UNAVAILABLE");
    }
    let parsed: ApiEnvelope<T> & ApiErrorEnvelope;
    try {
      parsed = await response.json() as ApiEnvelope<T> & ApiErrorEnvelope;
    } catch {
      throw new ReviewApiError(response.status, "INVALID_REVIEW_RESPONSE");
    }
    if (!parsed || typeof parsed !== "object" || !response.ok || !("data" in parsed)) {
      throw new ReviewApiError(response.status, parsed?.error?.code ?? "REVIEW_REQUEST_REJECTED", parsed?.meta?.requestId);
    }
    return parsed.data;
  }

  async function mutate<T>(path: string, payload: Record<string, unknown>, options: { publicRequest?: boolean; sessionId?: string } = {}, version?: number): Promise<T> {
    const body = JSON.stringify(payload);
    const identity = JSON.stringify([path, options.sessionId, body, version]);
    const key = retries.get(identity) ?? uniqueKey();
    retries.set(identity, key);
    try {
      const result = await request<T>(path, { method: "POST", headers: { "Idempotency-Key": key, ...(version ? { "If-Match": String(version) } : {}) }, body }, options);
      retries.delete(identity);
      return result;
    } catch (error) {
      if (error instanceof ReviewApiError && error.status >= 400 && error.status < 500) retries.delete(identity);
      throw error;
    }
  }

  return {
    projectReviewDefaults: async (projectId, signal) => {
      const project = await request<{ configuration?: { clientCanComment?: boolean; clientCanApprove?: boolean; clientCanDownload?: boolean } }>(`/projects/${encodeURIComponent(projectId)}`, { signal });
      return { comment: project.configuration?.clientCanComment ?? true, approve: project.configuration?.clientCanApprove ?? true, download: project.configuration?.clientCanDownload ?? false };
    },
    openPublicContext: (token, accessSecret, options) => request<PublicReviewResult>(`/review-links/${encodeURIComponent(token)}/context`, { signal: options?.signal }, { publicRequest: true, accessSecret, sessionId: options?.sessionId }),
    publicAction: (token, sessionId, payload) => mutate(`/review-links/${encodeURIComponent(token)}/actions`, payload, { publicRequest: true, sessionId }),
    uploadScreenshot: async (token, sessionId, file, timecodeMs) => {
      const result = await request<{ screenshot: { id: string } }>(`/review-links/${encodeURIComponent(token)}/screenshots?timecodeMs=${timecodeMs}`, {
        method: "POST", headers: { "Content-Type": file.type }, body: file,
      }, { publicRequest: true, sessionId });
      return result.screenshot;
    },
    logout: async (token, sessionId) => { await request(`/review-links/${encodeURIComponent(token)}/logout`, { method: "POST" }, { publicRequest: true, sessionId }); },
    mediaUrl: (token, sessionId, ticket, download = false) => `${baseUrl}/review-links/${encodeURIComponent(token)}/media?session=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}${download ? "&download=true" : ""}`,
    screenshotUrl: (token, sessionId, ticket, screenshotId) => `${baseUrl}/review-links/${encodeURIComponent(token)}/screenshots/${encodeURIComponent(screenshotId)}?session=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}`,
    projectLinks: (projectId, options) => request<ReviewPage>(`/projects/${encodeURIComponent(projectId)}/reviews${listQuery(options)}`, { signal: options?.signal }),
    workspaceLinks: (workspaceId, options) => request<ReviewPage>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links${listQuery(options)}`, { signal: options?.signal }),
    createProjectLink: async (projectId, input) => (await mutate<{ reviewLink: ReviewLink }>(`/projects/${encodeURIComponent(projectId)}/review-links`, { ...input })).reviewLink,
    createWorkspaceLink: async (workspaceId, input) => (await mutate<{ reviewLink: ReviewLink }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links`, { ...input })).reviewLink,
    revokeWorkspaceLink: async (workspaceId, linkId, version) => (await mutate<{ reviewLink: ReviewLink }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links/${encodeURIComponent(linkId)}/revoke`, { version }, {}, version)).reviewLink,
    reviewDetail: (workspaceId, linkId, signal) => request(`/workspaces/${encodeURIComponent(workspaceId)}/review-links/${encodeURIComponent(linkId)}`, { signal }),
    changeCommentStatus: async (workspaceId, commentId, status, version) => (await mutate<{ comment: ReviewComment }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-comments/${encodeURIComponent(commentId)}/status`, { status, version }, {}, version)).comment,
    replyToComment: async (workspaceId, commentId, body) => (await mutate<{ comment: ReviewComment }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-comments/${encodeURIComponent(commentId)}/replies`, { body })).comment,
  };
}

function listQuery(options?: ListOptions): string {
  const params = new URLSearchParams();
  if (options?.query) params.set("q", options.query);
  if (options?.pageToken) params.set("pageToken", options.pageToken);
  params.set("pageSize", String(options?.pageSize ?? 20));
  return `?${params.toString()}`;
}
