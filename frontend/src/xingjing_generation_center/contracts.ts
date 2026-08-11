export type GenerationTaskStatus = "queued" | "running" | "retrying" | "cancelling" | "succeeded" | "failed" | "timeout" | "cancelled";
export type GenerationMediaType = "image" | "video";

export interface GenerationRequest {
  readonly workspace_id: string;
  readonly project_id: string;
  readonly media_type: GenerationMediaType;
  readonly capability: string;
  readonly prompt: string;
  readonly parameters: Record<string, unknown>;
  readonly input_asset_ids: readonly string[];
  readonly requested_provider_id: string | null;
  readonly requested_model_id: string | null;
}

export interface GenerationFailure {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
}

export interface GenerationTask {
  readonly task_id: string;
  readonly request: GenerationRequest;
  readonly status: GenerationTaskStatus;
  readonly attempt: number;
  readonly version: number;
  readonly resolved_provider_id: string | null;
  readonly resolved_model_id: string | null;
  readonly provider_job_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly timeout_at: string;
  readonly completed_at: string | null;
  readonly failure: GenerationFailure | null;
  readonly output_asset_ids: readonly string[];
}

export interface GeneratedAsset {
  readonly asset_id: string;
  readonly workspace_id: string;
  readonly project_id: string;
  readonly task_id: string;
  readonly media_type: GenerationMediaType;
  readonly object_key: string;
  readonly content_sha256: string;
  readonly size_bytes: number;
  readonly metadata: Record<string, unknown>;
  readonly created_at: string;
}

export interface GeneratedCandidateSelection {
  readonly workspace_id: string;
  readonly project_id: string;
  readonly task_id: string;
  readonly selected_asset_id: string;
  readonly version: number;
  readonly actor_id: string;
  readonly request_id: string;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface GenerationCostEvidence {
  readonly evidence_id: string;
  readonly task_id: string;
  readonly workspace_id: string;
  readonly project_id: string;
  readonly provider_call_evidence_id: string;
  readonly currency: string;
  readonly estimated_minor: number;
  readonly actual_minor: number;
  readonly pricing_version: string;
  readonly recorded_at: string;
}

export interface GenerationModelAvailability {
  readonly provider_id: string | null;
  readonly model_id: string | null;
  readonly submission_available: boolean;
  readonly callback_available: boolean;
  readonly billing_currency: string | null;
  readonly estimated_cost_minor: number | null;
  readonly pricing_version: string | null;
  readonly models?: readonly {
    readonly provider_id: string;
    readonly model_id: string;
    readonly display_name: string;
    readonly version: string;
    readonly region: string;
    readonly media_types: readonly GenerationMediaType[];
    readonly capabilities: readonly string[];
    readonly active: boolean;
  }[];
}

export interface GenerationModelUsageCost {
  readonly currency: string;
  readonly estimated_minor: number;
  readonly actual_minor: number;
}

export interface GenerationModelUsageReport {
  readonly provider_id: string | null;
  readonly model_id: string | null;
  readonly task_count: number;
  readonly succeeded_count: number;
  readonly processing_count: number;
  readonly failed_count: number;
  readonly costs: readonly GenerationModelUsageCost[];
}

export interface GenerationCandidateSet {
  readonly task: GenerationTask;
  readonly assets: readonly GeneratedAsset[];
  readonly selectedCandidate: GeneratedCandidateSelection | null;
}

export interface GenerationTaskPage {
  readonly tasks: readonly GenerationTask[];
  readonly total: number;
  readonly requestId: string | null;
}

export interface GenerationTaskDetail {
  readonly task: GenerationTask;
  readonly generatedAssets: readonly GeneratedAsset[];
  readonly selectedCandidate: GeneratedCandidateSelection | null;
  readonly costEvidence: readonly GenerationCostEvidence[];
  readonly billing: {
    readonly currency: string;
    readonly estimated_minor: number;
    readonly actual_minor: number;
    readonly released_minor: number;
    readonly pricing_version: string;
    readonly status: "active" | "settled" | "released";
  } | null;
  readonly progress: {
    readonly queue_position: number | null;
    readonly latest: {
      readonly event_id: string;
      readonly percent: number;
      readonly message: string;
      readonly eta_seconds: number | null;
      readonly occurred_at: string;
    } | null;
  };
  readonly requestId: string | null;
}

export interface GenerationTaskFilters {
  readonly status?: GenerationTaskStatus;
  readonly mediaType?: GenerationMediaType;
  readonly capability?: string;
}

export interface GenerationCandidatePage {
  readonly candidates: readonly GenerationCandidateSet[];
  readonly total: number;
  readonly requestId: string | null;
}

export interface GenerationWorkspaceScope {
  readonly workspaceId: string;
  readonly projectId: string;
  readonly projectName?: string;
}

export type GenerationWorkbenchView =
  | "center" | "image" | "image-to-video" | "text-to-video" | "video" | "tasks"
  | "first-last-video" | "image-edit" | "inpaint" | "reference-fusion" | "image-upscale"
  | "model-center" | "model-detail" | "model-market" | "model-report"
  | "video-candidates" | "video-extend" | "video-retry" | "video-stabilize" | "video-upscale";

export interface GenerationSubmission {
  readonly media_type: GenerationMediaType;
  readonly capability: string;
  readonly prompt: string;
  readonly parameters?: Record<string, unknown>;
  readonly input_asset_ids?: readonly string[];
  readonly requested_provider_id?: string;
  readonly requested_model_id?: string;
}

export const TERMINAL_TASK_STATUSES: readonly GenerationTaskStatus[] = ["succeeded", "failed", "timeout", "cancelled"];
export const PROCESSING_TASK_STATUSES: readonly GenerationTaskStatus[] = ["queued", "running", "retrying", "cancelling"];

export function isProcessingTask(status: GenerationTaskStatus): boolean {
  return PROCESSING_TASK_STATUSES.includes(status);
}

export function canCancelTask(status: GenerationTaskStatus): boolean {
  return status === "queued" || status === "retrying" || status === "running";
}

export function canRetryTask(status: GenerationTaskStatus): boolean {
  return status === "failed" || status === "timeout" || status === "cancelled";
}

export const taskStatusText: Record<GenerationTaskStatus, string> = {
  queued: "排队中",
  running: "生成中",
  retrying: "重试中",
  cancelling: "取消中",
  succeeded: "已完成",
  failed: "生成失败",
  timeout: "已超时",
  cancelled: "已取消",
};
