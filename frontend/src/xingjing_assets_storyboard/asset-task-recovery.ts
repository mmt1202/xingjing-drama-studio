import type { TaskReceipt } from "./contracts";
import { getSessionEpoch } from "@/utils/auth";

export function assetTaskRecoveryKey(actorId: string, workspaceId: string, projectId: string, assetId: string, epoch = getSessionEpoch()) {
  return `asset-generation:${JSON.stringify([epoch, actorId, workspaceId, projectId, assetId])}`;
}

export type RecoverableAssetAction = "generateReferenceImage" | "generateTurnaround" | "generateExpressions" | "adoptGenerationOutput";
export interface PendingAssetSubmission {
  readonly action: RecoverableAssetAction;
  readonly targetId: string;
  readonly projectId?: string;
  readonly version: number;
  readonly payload: Record<string, unknown>;
  readonly idempotencyKey: string;
}
export function isRecoverableAssetAction(action: string): action is RecoverableAssetAction {
  return ["generateReferenceImage", "generateTurnaround", "generateExpressions", "adoptGenerationOutput"].includes(action);
}
export function readPendingAssetSubmission(key: string): PendingAssetSubmission | undefined {
  try {
    const raw = sessionStorage.getItem(`${key}:pending`);
    if (!raw || raw.length > 256_000) return undefined;
    const value = JSON.parse(raw) as Partial<PendingAssetSubmission> | null;
    if (!value || typeof value.action !== "string" || !isRecoverableAssetAction(value.action)
      || typeof value.targetId !== "string" || !value.targetId || value.targetId.length > 128
      || typeof value.idempotencyKey !== "string" || !value.idempotencyKey || value.idempotencyKey.length > 128
      || !Number.isSafeInteger(value.version) || (value.version ?? 0) < 1
      || (value.projectId !== undefined && (typeof value.projectId !== "string" || !value.projectId || value.projectId.length > 128))
      || !value.payload || typeof value.payload !== "object" || Array.isArray(value.payload)) return undefined;
    return value as PendingAssetSubmission;
  } catch { return undefined; }
}
/** Save the exact command before sending it; restoring this record never submits it. */
export function rememberPendingAssetSubmission(key: string, value: PendingAssetSubmission): boolean {
  try {
    const serialized = JSON.stringify(value);
    if (serialized.length > 256_000) return false;
    sessionStorage.setItem(`${key}:pending`, serialized); return true;
  } catch { return false; }
}
export function clearPendingAssetSubmission(key: string): boolean {
  try { sessionStorage.removeItem(`${key}:pending`); return true; } catch { return false; }
}

export function readAssetTask(key: string): TaskReceipt | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null") as Partial<TaskReceipt> | null;
    if (!value || typeof value.taskId !== "string" || !value.taskId || typeof value.projectId !== "string" || !value.projectId) return undefined;
    return { taskId: value.taskId, projectId: value.projectId, status: "queued" };
  } catch { return undefined; }
}

export function rememberAssetTask(key: string, task: TaskReceipt) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ taskId: task.taskId, projectId: task.projectId }));
    return true;
  } catch { return false; }
}
