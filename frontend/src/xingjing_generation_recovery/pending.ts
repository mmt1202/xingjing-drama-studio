import { getSessionEpoch } from "@/utils/auth";
import type { ResolvePayload } from "./api";

export interface PendingResolution { projectId: string; taskId: string; key: string; payload: ResolvePayload }
export function pendingKey(workspace: string, actor: string) {
  return `generation-reconciliation:${JSON.stringify([getSessionEpoch(), workspace, actor])}`;
}
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
export function readPending(key: string): PendingResolution | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 8000) return undefined;
    const value = JSON.parse(raw) as Partial<PendingResolution> | null;
    if (!value || !identifier(value.projectId) || !identifier(value.taskId) || !identifier(value.key)
      || !value.payload || !/^[a-f0-9-]{36}$/i.test(value.payload.previewId) || typeof value.payload.reason !== "string"
      || value.payload.reason.trim().length < 5 || value.payload.reason.length > 1000) return undefined;
    return { projectId: value.projectId, taskId: value.taskId, key: value.key,
      payload: { previewId: value.payload.previewId, reason: value.payload.reason } };
  } catch { return undefined; }
}
/** Save before POST. Restoring is read-only until the user explicitly retries. */
export function savePending(key: string, value?: PendingResolution): boolean {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value));
    else sessionStorage.removeItem(key);
    return true;
  } catch { return false; }
}
