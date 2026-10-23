import type { VersionRef } from "./contracts";

export interface SavedVersionDraft {
  action: "snapshot" | "lock" | "restore"; ref: VersionRef; expectedRevision: number; sourceDigest: string; label: string; note: string;
}
const key = (actor: string, workspace: string, project: string) => `version-action-draft:${JSON.stringify([actor, workspace, project])}`;
export function readVersionDraft(actor: string, workspace: string, project: string): SavedVersionDraft | undefined {
  try {
    const value = JSON.parse(sessionStorage.getItem(key(actor, workspace, project)) ?? "null") as Partial<SavedVersionDraft> | null;
    const ref = value?.ref;
    if (!value || !["snapshot", "lock", "restore"].includes(value.action ?? "") || !ref
      || !["script", "asset", "shot", "storyboard", "final"].includes(ref.kind)
      || ![ref.objectId, ref.versionId].every((item) => typeof item === "string" && item.trim().length > 0 && item.length <= 128)
      || ((ref.kind === "shot") !== (typeof ref.shotId === "string" && ref.shotId.length > 0 && ref.shotId.length <= 128))
      || !Number.isSafeInteger(value.expectedRevision) || (value.expectedRevision ?? -1) < 0
      || typeof value.sourceDigest !== "string" || !/^sha256:[a-f0-9]{64}$/.test(value.sourceDigest)
      || typeof value.label !== "string" || value.label.length > 160 || typeof value.note !== "string" || value.note.length > 1000) return;
    return value as SavedVersionDraft;
  } catch { return; }
}
export function saveVersionDraft(actor: string, workspace: string, project: string, value?: SavedVersionDraft) {
  try {
    if (value && (value.label.trim() || value.note.trim())) sessionStorage.setItem(key(actor, workspace, project), JSON.stringify(value));
    else sessionStorage.removeItem(key(actor, workspace, project));
  } catch { /* In-memory editing remains available when session storage is disabled. */ }
}
