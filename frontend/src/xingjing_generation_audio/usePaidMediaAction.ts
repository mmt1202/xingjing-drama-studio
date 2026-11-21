import { useEffect, useRef, useState } from "react";
import { getSessionEpoch, getTokenForSession } from "@/utils/auth";
import { createIdempotencyKey } from "./contracts";
import {
  paidAudioRecoveryKey,
  readPaidAudioOperation,
  rememberPaidAudioOperation,
  type PaidAudioOperation,
  type PaidAudioPayload,
} from "./paid-request-recovery";
import { XingjingApiError } from "./api";
import type { AudioClient } from "./GenerationAudioWorkspace";

export function usePaidMediaAction(
  api: AudioClient,
  projectId: string,
  workspaceId: string | undefined,
  epoch: string,
  onSuccess: () => void,
  onFailure: (cause: unknown) => void,
) {
  const key = paidAudioRecoveryKey(epoch, workspaceId, projectId);
  const [original, setOriginal] = useState(() => readPaidAudioOperation(key));
  const pending = useRef(original);
  const inflight = useRef(false);
  const alive = useRef(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  async function execute(operation: PaidAudioOperation) {
    if (inflight.current || epoch !== getSessionEpoch()) return;
    if (!rememberPaidAudioOperation(key, operation)) {
      onFailure(new Error("recoveryUnavailable"));
      return;
    }
    pending.current = operation;
    setOriginal(operation);
    inflight.current = true;
    setBusy(true);
    const clear = () => {
      rememberPaidAudioOperation(key, undefined);
      pending.current = undefined;
      setOriginal(undefined);
    };
    try {
      getTokenForSession(epoch);
      await api.audioAction(projectId, operation.payload, operation.key, operation.version);
      if (!alive.current || epoch !== getSessionEpoch()) return;
      clear();
      onSuccess();
    } catch (error) {
      if (!alive.current || epoch !== getSessionEpoch()) return;
      if (
        error instanceof XingjingApiError &&
        error.detail.status >= 400 &&
        error.detail.status < 500 &&
        ![401, 408].includes(error.detail.status)
      )
        clear();
      onFailure(error);
    } finally {
      inflight.current = false;
      if (alive.current && epoch === getSessionEpoch()) setBusy(false);
    }
  }
  function submit(payload: PaidAudioPayload, version?: number) {
    if (pending.current || inflight.current) return;
    void execute({
      key: createIdempotencyKey(projectId, payload.action, crypto.randomUUID()),
      payload,
      version,
    });
  }
  return { original, busy, pending, execute, submit };
}
