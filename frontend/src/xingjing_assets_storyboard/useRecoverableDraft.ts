import { useEffect, useState } from "react";
import { getSessionEpoch } from "@/utils/auth";
import { clearProductionDraft, productionDraftSlot, readProductionDraft, writeProductionDraft, type ProductionDraftScope } from "./draft-state";

export function useRecoverableDraft<T>({ scope, kind, objectId, sourceVersion, currentSourceVersion = sourceVersion, value, dirty, validate }: {
  scope: ProductionDraftScope; kind: "asset" | "smart"; objectId: string; sourceVersion: string; currentSourceVersion?: string;
  value: T; dirty: boolean; validate: (value: unknown) => value is T;
}) {
  const [slot] = useState(() => productionDraftSlot(scope, kind, objectId));
  const [pending, setPending] = useState(() => readProductionDraft(slot, validate));
  const [storageUnavailable, setStorageUnavailable] = useState(false);
  useEffect(() => {
    if (pending) return;
    if (dirty) {
      if (!writeProductionDraft(slot, sourceVersion, value)) void Promise.resolve().then(() => setStorageUnavailable(true));
    } else clearProductionDraft(slot);
  }, [slot, sourceVersion, value, dirty, pending]);
  const canResume = Boolean(pending && pending.sourceVersion === currentSourceVersion && getSessionEpoch() === slot.epoch);
  return {
    pending, canResume, storageUnavailable,
    resume: (apply: (value: T) => void) => { if (pending && canResume) { apply(pending.value); setPending(undefined); } },
    clear: () => { clearProductionDraft(slot); setPending(undefined); },
  };
}
