import { getSessionEpoch } from "@/utils/auth";
import type { Candidate, PendingOperation } from "./api";

export interface PlanDraft { planId?: string; candidates?: Candidate[]; included?: string[]; pending?: PendingOperation }
export function storageKey(workspace: string, actor: string, project: string) {
  return `source-plan:${JSON.stringify([getSessionEpoch(), workspace, actor, project])}`;
}
const identity = (value: unknown): value is string => typeof value === "string" && /^[a-z0-9][a-z0-9._:/-]{0,127}$/i.test(value);
const candidate = (value: unknown): value is Candidate => {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return identity(item.id) && typeof item.title === "string" && item.title.length <= 200
    && [item.start, item.end, item.targetDuration].every(Number.isSafeInteger);
};
const candidates = (value: unknown): value is Candidate[] => Array.isArray(value) && value.length <= 200 && value.every(candidate);
export function readDraft(key: string): PlanDraft {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 400_000) return {};
    const value = JSON.parse(raw) as PlanDraft;
    if (!value || typeof value !== "object" || value.planId !== undefined && !identity(value.planId)
      || value.candidates !== undefined && !candidates(value.candidates)
      || value.included !== undefined && (!Array.isArray(value.included) || value.included.length > 200 || !value.included.every(identity))) return {};
    if (value.pending) {
      const pending = value.pending;
      if (!identity(pending.key)) return {};
      if (pending.kind === "confirm") {
        if (!identity(pending.planId) || !candidates(pending.payload?.candidates) || !pending.payload.candidates.length) return {};
      } else if (pending.kind === "create") {
        const payload = pending.payload;
        if (!payload || typeof payload.sourceId !== "string" || payload.sourceId.length > 1024
          || !/^[a-f0-9]{64}$/.test(payload.sourceDigest) || !/^[a-f0-9]{64}$/.test(payload.episodeRevision)
          || !Number.isSafeInteger(payload.count) || payload.count < 1 || payload.count > 200
          || !Array.isArray(payload.chapterIds) || payload.chapterIds.length > 4096 || !payload.chapterIds.every(identity)) return {};
      } else return {};
    }
    return value;
  } catch { return {}; }
}
export function storeDraft(key: string, value: PlanDraft): boolean {
  try { sessionStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
}
