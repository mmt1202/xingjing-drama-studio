import { getSessionEpoch } from "@/utils/auth";
import type { GenerationSubmission } from "./contracts";
import type { SelectedGenerationInput } from "./GenerationInputPicker";

export interface SubmissionOperation { key: string; payload: GenerationSubmission }
export interface SubmissionRecovery {
  prompt: string;
  inputs: readonly SelectedGenerationInput[];
  parameters: string;
  modelChoice: string;
  operation?: SubmissionOperation;
  created?: string;
}
export function submissionRecoveryKey(workspaceId: string, projectId: string, capability: string, media: string) {
  return `generation-draft:${JSON.stringify([getSessionEpoch(), workspaceId, projectId, capability, media])}`;
}
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 512;
function input(value: unknown): value is SelectedGenerationInput {
  if (!record(value) || !identifier(value.key) || !identifier(value.assetId) || !identifier(value.generatedAssetId)
    || typeof value.label !== "string" || value.label.length > 2000 || !["image", "video"].includes(String(value.mediaType))) return false;
  const ref = value.reference;
  return ref === undefined || (record(ref) && ref.asset_id === value.assetId && identifier(ref.asset_version_id)
    && (ref.asset_content_sha256 === undefined || (typeof ref.asset_content_sha256 === "string" && /^[a-f0-9]{64}$/.test(ref.asset_content_sha256))));
}
export function readSubmissionRecovery(key: string, capability: string, media: string): SubmissionRecovery | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 256_000) return undefined;
    const value: unknown = JSON.parse(raw);
    if (!record(value) || typeof value.prompt !== "string" || value.prompt.length > 12000
      || typeof value.parameters !== "string" || value.parameters.length > 32000
      || typeof value.modelChoice !== "string" || value.modelChoice.length > 2000
      || !Array.isArray(value.inputs) || value.inputs.length > 12 || !value.inputs.every(input)
      || (value.created !== undefined && !identifier(value.created))) return undefined;
    if (value.operation !== undefined) {
      const op = value.operation;
      if (!record(op) || !identifier(op.key) || !record(op.payload) || op.payload.capability !== capability || op.payload.media_type !== media
        || typeof op.payload.prompt !== "string" || !op.payload.prompt.trim() || op.payload.prompt.length > 12000
        || !record(op.payload.parameters) || !Array.isArray(op.payload.input_asset_ids) || op.payload.input_asset_ids.length > 12
        || !op.payload.input_asset_ids.every(identifier) || (op.payload.input_asset_references !== undefined && !Array.isArray(op.payload.input_asset_references))) return undefined;
    }
    return value as unknown as SubmissionRecovery;
  } catch { return undefined; }
}
/** Persist request identity before submission; restoring never sends a request automatically. */
export function rememberSubmission(key: string, value: SubmissionRecovery): boolean {
  try {
    if (!value.operation && !value.created && !value.prompt && !value.inputs.length && value.parameters === "{}" && !value.modelChoice) {
      sessionStorage.removeItem(key);
    } else {
      const serialized = JSON.stringify(value);
      if (serialized.length > 256_000) return false;
      sessionStorage.setItem(key, serialized);
    }
    return true;
  } catch { return false; }
}
