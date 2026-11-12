import type { BudgetScope } from "@/xingjing_budget/contracts";
import type { MediaTaskKind } from "./contracts";

export type PaidAudioPayload = {
  action: "create_media_task"; taskId: string; taskKind: MediaTaskKind;
  resourceType: string; resourceId: string; maxAttempts: number; budgetScope?: BudgetScope;
} | { action: "retry_media_task"; taskId: string };
export interface PaidAudioOperation { key: string; payload: PaidAudioPayload; version?: number }

const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 512;
export function paidAudioRecoveryKey(epoch: string, workspaceId: string | undefined, projectId: string) {
  return `audio-paid-request:${JSON.stringify([epoch, workspaceId, projectId])}`;
}
export function readPaidAudioOperation(key: string): PaidAudioOperation | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 12_000) return undefined;
    const value: unknown = JSON.parse(raw);
    if (!record(value) || !identifier(value.key) || !record(value.payload) || !identifier(value.payload.taskId)) return undefined;
    const payload = value.payload;
    if (payload.action === "retry_media_task") {
      if (typeof value.version !== "number" || !Number.isSafeInteger(value.version) || value.version < 1) return undefined;
    } else if (payload.action === "create_media_task") {
      if (!["audio", "subtitle", "lip_sync", "mix"].includes(String(payload.taskKind)) || !identifier(payload.resourceType)
        || !identifier(payload.resourceId) || typeof payload.maxAttempts !== "number" || !Number.isSafeInteger(payload.maxAttempts)
        || payload.maxAttempts < 1 || payload.maxAttempts > 20) return undefined;
      const scope = payload.budgetScope;
      if (scope !== undefined && (!record(scope) || (scope.kind !== "project_shared" && (scope.kind !== "episode"
        || typeof scope.episodeId !== "number" || !Number.isSafeInteger(scope.episodeId) || scope.episodeId < 1)))) return undefined;
    } else return undefined;
    return value as unknown as PaidAudioOperation;
  } catch { return undefined; }
}
/** Keep the request identity before a paid write; restoring never submits automatically. */
export function rememberPaidAudioOperation(key: string, operation: PaidAudioOperation | undefined): boolean {
  try {
    if (operation) sessionStorage.setItem(key, JSON.stringify(operation));
    else sessionStorage.removeItem(key);
    return true;
  } catch { return false; }
}
