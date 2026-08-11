export type CommercialApiErrorKind =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "validation"
  | "precondition"
  | "conflict"
  | "rate_limited"
  | "upstream"
  | "server"
  | "network"
  | "contract";

export type CommercialConflictKind =
  | "version"
  | "idempotency"
  | "state"
  | "settlement"
  | "amount"
  | "unknown";

export interface CommercialApiErrorOptions {
  readonly message: string;
  readonly kind: CommercialApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly requiresRefresh: boolean;
  readonly conflict: CommercialConflictKind | null;
  readonly details: unknown;
}

export class CommercialApiError extends Error {
  readonly kind: CommercialApiErrorKind;
  readonly status: number | null;
  readonly code: string;
  readonly requestId: string | null;
  readonly retryable: boolean;
  readonly requiresRefresh: boolean;
  readonly conflict: CommercialConflictKind | null;
  readonly details: unknown;

  constructor(options: CommercialApiErrorOptions) {
    super(options.message);
    this.name = "CommercialApiError";
    this.kind = options.kind;
    this.status = options.status;
    this.code = options.code;
    this.requestId = options.requestId;
    this.retryable = options.retryable;
    this.requiresRefresh = options.requiresRefresh;
    this.conflict = options.conflict;
    this.details = options.details;
  }
}
