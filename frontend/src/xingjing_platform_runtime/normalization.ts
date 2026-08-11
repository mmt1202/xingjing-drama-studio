export type TaskPhase = "pending" | "active" | "succeeded" | "failed" | "cancelled";

export interface NormalizedTask {
  readonly id: string;
  readonly phase: TaskPhase;
  readonly sourceStatus: string;
  readonly terminal: boolean;
  readonly progress: number | null;
  readonly canRetry: boolean;
  readonly canCancel: boolean;
}

const ACTIVE = new Set(["queued", "running", "retrying", "settling"]);
const FAILED = new Set(["failed", "timeout"]);

function normalizeId(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

export function normalizeTask(value: Record<string, unknown>): NormalizedTask {
  const status = typeof value.status === "string" ? value.status.toLowerCase() : "pending";
  const phase: TaskPhase = status === "succeeded" || status === "completed" ? "succeeded"
    : FAILED.has(status) ? "failed" : status === "cancelled" || status === "canceled" ? "cancelled"
      : ACTIVE.has(status) ? "active" : "pending";
  const rawProgress = typeof value.progress === "number" && Number.isFinite(value.progress) ? value.progress : null;
  return {
    id: normalizeId(value.id),
    phase,
    sourceStatus: status,
    terminal: phase === "succeeded" || phase === "failed" || phase === "cancelled",
    progress: rawProgress === null ? null : Math.min(100, Math.max(0, rawProgress)),
    canRetry: FAILED.has(status),
    canCancel: ACTIVE.has(status) || phase === "pending",
  };
}

export interface NormalizedNotification {
  readonly id: string;
  readonly category: string;
  readonly read: boolean;
  readonly createdAt: string;
  readonly aggregateCount: number;
}

export function normalizeNotification(value: Record<string, unknown>): NormalizedNotification {
  return {
    id: normalizeId(value.id),
    category: typeof value.category === "string" ? value.category : "system",
    read: typeof value.read === "boolean" ? value.read : Boolean(value.read_at),
    createdAt: typeof value.created_at === "string" ? value.created_at : "",
    aggregateCount: typeof value.aggregate_count === "number" ? Math.max(1, value.aggregate_count) : 1,
  };
}
