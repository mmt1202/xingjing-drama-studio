import { getSessionEpoch } from "@/utils/auth";

export interface ProductionDraftScope { actorId: string; workspaceId: string; projectId: string | null }
export interface DraftSlot { key: string; epoch: string }
export interface SavedProductionDraft<T> { version: 1; sourceVersion: string; value: T }
export function productionDraftSlot(scope: ProductionDraftScope, kind: "asset" | "smart", objectId: string): DraftSlot {
  const epoch = getSessionEpoch();
  return { epoch, key: "xingjing:production-draft:" + JSON.stringify([epoch, scope.actorId, scope.workspaceId, scope.projectId, kind, objectId]) };
}
export function readProductionDraft<T>(slot: DraftSlot, validate: (value: unknown) => value is T): SavedProductionDraft<T> | undefined {
  if (getSessionEpoch() !== slot.epoch) return;
  try {
    const raw = sessionStorage.getItem(slot.key);
    if (!raw) return;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return;
    const candidate = parsed as Partial<SavedProductionDraft<unknown>>;
    if (candidate.version !== 1 || typeof candidate.sourceVersion !== "string" || !validate(candidate.value)) return;
    return candidate as SavedProductionDraft<T>;
  } catch { return; }
}
export function writeProductionDraft<T>(slot: DraftSlot, sourceVersion: string, value: T): boolean {
  if (getSessionEpoch() !== slot.epoch) return false;
  try { sessionStorage.setItem(slot.key, JSON.stringify({ version: 1, sourceVersion, value })); return true; }
  catch { return false; }
}
export function clearProductionDraft(slot: DraftSlot) {
  if (getSessionEpoch() !== slot.epoch) return;
  try { sessionStorage.removeItem(slot.key); } catch { /* Storage availability cannot cancel an acknowledged server write. */ }
}
