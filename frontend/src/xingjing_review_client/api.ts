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

export interface ReviewPage { reviewLinks: ReviewLink[]; nextToken: string | null }
export interface ListOptions { query?: string; pageToken?: string; pageSize?: number }

export class ReviewApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) {
    super(code);
    this.name = "ReviewApiError";
  }
}

interface ApiEnvelope<T> { data: T; meta?: { requestId?: string } }
interface ApiErrorEnvelope { error?: { code?: string }; meta?: { requestId?: string } }

export interface ReviewApi {
  openPublicContext(token: string, accessSecret?: string): Promise<PublicReviewContext>;
  publicAction(token: string, sessionId: string, payload: Record<string, unknown>): Promise<{ comment?: ReviewComment; delivery?: ReviewDelivery }>;
  uploadScreenshot(token: string, sessionId: string, file: File, timecodeMs: number): Promise<{ id: string }>;
  logout(token: string, sessionId: string): Promise<void>;
  mediaUrl(token: string, sessionId: string, ticket: string, download?: boolean): string;
  screenshotUrl(token: string, sessionId: string, ticket: string, screenshotId: string): string;
  projectLinks(projectId: string, options?: ListOptions): Promise<ReviewPage>;
  workspaceLinks(workspaceId: string, options?: ListOptions): Promise<ReviewPage>;
  createProjectLink(projectId: string, input: CreateReviewLinkInput): Promise<ReviewLink>;
  createWorkspaceLink(workspaceId: string, input: CreateReviewLinkInput & { projectId: string }): Promise<ReviewLink>;
  revokeWorkspaceLink(workspaceId: string, linkId: string, version: number): Promise<ReviewLink>;
  reviewDetail(workspaceId: string, linkId: string): Promise<{ reviewLink: ReviewLink; comments: ReviewComment[] }>;
  changeCommentStatus(workspaceId: string, commentId: string, status: string, version: number): Promise<ReviewComment>;
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
  async function request<T>(path: string, init: RequestInit = {}, options: { publicRequest?: boolean; sessionId?: string; accessSecret?: string } = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Request-Id", uniqueKey());
    if (init.body) headers.set("Content-Type", "application/json");
    if (!options.publicRequest && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    if (options.sessionId) headers.set("X-Review-Session-Id", options.sessionId);
    if (options.accessSecret) headers.set("X-Review-Access-Secret", options.accessSecret);

    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, { ...init, headers });
    } catch {
      throw new ReviewApiError(0, "NETWORK_UNAVAILABLE");
    }
    let parsed: ApiEnvelope<T> & ApiErrorEnvelope;
    try {
      parsed = await response.json() as ApiEnvelope<T> & ApiErrorEnvelope;
    } catch {
      throw new ReviewApiError(response.status, "INVALID_REVIEW_RESPONSE");
    }
    if (!response.ok || !("data" in parsed)) {
      throw new ReviewApiError(response.status, parsed.error?.code ?? "REVIEW_REQUEST_REJECTED", parsed.meta?.requestId);
    }
    return parsed.data;
  }

  return {
    openPublicContext: (token, accessSecret) => request<PublicReviewContext>(`/review-links/${encodeURIComponent(token)}/context`, {}, { publicRequest: true, accessSecret }),
    publicAction: (token, sessionId, payload) => request(`/review-links/${encodeURIComponent(token)}/actions`, {
      method: "POST",
      headers: { "Idempotency-Key": uniqueKey() },
      body: JSON.stringify(payload),
    }, { publicRequest: true, sessionId }),
    uploadScreenshot: async (token, sessionId, file, timecodeMs) => {
      const response = await fetcher(`${baseUrl}/review-links/${encodeURIComponent(token)}/screenshots?timecodeMs=${timecodeMs}`, {
        method: "POST", headers: { "Content-Type": file.type, "X-Review-Session-Id": sessionId, "X-Request-Id": uniqueKey() }, body: file,
      });
      const parsed = await response.json() as ApiEnvelope<{ screenshot: { id: string } }> & ApiErrorEnvelope;
      if (!response.ok || !("data" in parsed)) throw new ReviewApiError(response.status, parsed.error?.code ?? "SCREENSHOT_UPLOAD_FAILED", parsed.meta?.requestId);
      return parsed.data.screenshot;
    },
    logout: async (token, sessionId) => { await request(`/review-links/${encodeURIComponent(token)}/logout`, { method: "POST" }, { publicRequest: true, sessionId }); },
    mediaUrl: (token, sessionId, ticket, download = false) => `${baseUrl}/review-links/${encodeURIComponent(token)}/media?session=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}${download ? "&download=true" : ""}`,
    screenshotUrl: (token, sessionId, ticket, screenshotId) => `${baseUrl}/review-links/${encodeURIComponent(token)}/screenshots/${encodeURIComponent(screenshotId)}?session=${encodeURIComponent(sessionId)}&ticket=${encodeURIComponent(ticket)}`,
    projectLinks: (projectId, options) => request<ReviewPage>(`/projects/${encodeURIComponent(projectId)}/reviews${listQuery(options)}`),
    workspaceLinks: (workspaceId, options) => request<ReviewPage>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links${listQuery(options)}`),
    createProjectLink: async (projectId, input) => (await request<{ reviewLink: ReviewLink }>(`/projects/${encodeURIComponent(projectId)}/review-links`, {
      method: "POST", headers: { "Idempotency-Key": uniqueKey() }, body: JSON.stringify(input),
    })).reviewLink,
    createWorkspaceLink: async (workspaceId, input) => (await request<{ reviewLink: ReviewLink }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links`, {
      method: "POST", headers: { "Idempotency-Key": uniqueKey() }, body: JSON.stringify(input),
    })).reviewLink,
    revokeWorkspaceLink: async (workspaceId, linkId, version) => (await request<{ reviewLink: ReviewLink }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-links/${encodeURIComponent(linkId)}/revoke`, {
      method: "POST", headers: { "Idempotency-Key": uniqueKey(), "If-Match": String(version) }, body: JSON.stringify({ version }),
    })).reviewLink,
    reviewDetail: (workspaceId, linkId) => request(`/workspaces/${encodeURIComponent(workspaceId)}/review-links/${encodeURIComponent(linkId)}`),
    changeCommentStatus: async (workspaceId, commentId, status, version) => (await request<{ comment: ReviewComment }>(`/workspaces/${encodeURIComponent(workspaceId)}/review-comments/${encodeURIComponent(commentId)}/status`, {
      method: "POST", headers: { "Idempotency-Key": uniqueKey(), "If-Match": String(version) }, body: JSON.stringify({ status, version }),
    })).comment,
  };
}

function listQuery(options?: ListOptions): string {
  const params = new URLSearchParams();
  if (options?.query) params.set("q", options.query);
  if (options?.pageToken) params.set("pageToken", options.pageToken);
  params.set("pageSize", String(options?.pageSize ?? 20));
  return `?${params.toString()}`;
}
