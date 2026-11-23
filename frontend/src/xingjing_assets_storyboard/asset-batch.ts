import { AssetsStoryboardApiError } from "./api";
import {
  assetTaskRecoveryKey, clearPendingAssetSubmission, readAssetTask, readPendingAssetSubmission,
  rememberAssetTask, rememberPendingAssetSubmission, type PendingAssetSubmission,
} from "./asset-task-recovery";
import type { AssetItem, AssetsStoryboardPort, WorkspaceProjectScope } from "./contracts";

export type AssetBatchMode = "registerMany" | "reviewMany" | "imageMany";

export function batchEligible(mode: AssetBatchMode, asset: AssetItem) {
  if (mode === "registerMany") return ["missing", "expired", "blocked"].includes(asset.rightsStatus);
  if (mode === "reviewMany") return asset.rightsStatus === "pending" && Boolean(asset.rights?.id);
  return asset.type !== "voice" && Boolean(asset.currentVersionId);
}

export function batchError(cause: unknown) {
  if (cause instanceof AssetsStoryboardApiError) {
    if (cause.code === "GENERATION_INSUFFICIENT_CREDITS") return "batchCredit";
    if (cause.code === "GENERATION_WORKSPACE_CONCURRENCY_LIMIT") return "batchLimit";
    if (cause.code === "RIGHTS_EVIDENCE_NOT_VERIFIED") return "proofError";
    if (cause.code === "RIGHTS_EVIDENCE_EXPIRED") return "dateError";
    if (cause.status === 403) return "ownerOnly";
    if (cause.status === 409) return "conflict";
  }
  return "batchRetryHint";
}

export async function submitAssetImage(api: AssetsStoryboardPort, scope: WorkspaceProjectScope, actorId: string, asset: AssetItem):
Promise<{ label: "batchExisting" | "batchQueued" | "batchRecover"; taskId?: string }> {
  const projectId = scope.projectId ?? asset.projectId;
  if (!projectId || !asset.currentVersionId) throw new Error("missing image source");
  const slot = assetTaskRecoveryKey(actorId, scope.workspaceId, projectId, asset.id);
  const pending = readPendingAssetSubmission(slot);
  if (pending && (pending.action !== "generateReferenceImage" || pending.payload.sourceVersionId !== asset.currentVersionId)) {
    return { label: "batchRecover" };
  }
  const previous = readAssetTask(slot);
  if (!pending && previous) {
    const receipt = await api.getGenerationTask(scope, previous);
    if (["queued", "running", "retrying", "cancelling"].includes(receipt.status)) return { label: "batchExisting", taskId: receipt.taskId };
  }
  const command: PendingAssetSubmission = pending ?? {
    action: "generateReferenceImage", targetId: asset.id, projectId, version: asset.version,
    payload: { sourceVersionId: asset.currentVersionId, ownerProjectId: projectId },
    idempotencyKey: "asset-action:" + crypto.randomUUID(),
  };
  if (!rememberPendingAssetSubmission(slot, command)) throw new Error("recovery storage unavailable");
  try {
    const result = await api.assetAction(scope, command);
    if (!result.task) throw new Error("no task receipt");
    if (rememberAssetTask(slot, { ...result.task, projectId })) clearPendingAssetSubmission(slot);
    return { label: "batchQueued", taskId: result.task.taskId };
  } catch (cause) {
    if (cause instanceof AssetsStoryboardApiError && (
      (cause.status !== null && cause.status < 500 && cause.status !== 408) ||
      (typeof cause.details === "object" && cause.details !== null && "submissionState" in cause.details && cause.details.submissionState === "rejected")
    )) clearPendingAssetSubmission(slot);
    throw cause;
  }
}
