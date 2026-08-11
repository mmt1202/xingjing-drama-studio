export type ApiErrorKind = "unauthenticated" | "forbidden" | "conflict" | "rate_limited" | "server" | "network";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status: number | null,
    public readonly code: string | null,
    public readonly requestId: string | null,
    public readonly retryable: boolean,
    public readonly retryAfterMs: number | null = null,
    public readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function errorKind(status: number): ApiErrorKind {
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 409) return "conflict";
  if (status === 429) return "rate_limited";
  return "server";
}
