export type GenerationTaskStatus = "queued" | "running" | "retrying" | "cancelling" | "succeeded" | "failed" | "timeout" | "cancelled";
export type GenerationMediaType = "image" | "video";
export type ProductionGroup = "working" | "attention" | "completed" | "ended";
export type ProductionAction = "cancel" | "stop_waiting" | "retry";
export type ProductionState = GenerationTaskStatus | "checking" | "waiting_stopped" | "publishing";
export interface CreatorTaskInfo {
  readonly kind: "reference" | "shot" | "character_turnaround" | "character_expressions" | "generation";
  readonly subject: string | null;
  readonly assetKind: string | null;
  readonly sourceAssetId?: string | null;
  readonly episode: number | null;
  readonly shot: string | null;
  readonly state: ProductionState;
  readonly action: Exclude<ProductionAction, "retry"> | null;
  readonly canRetry: boolean;
  readonly canChooseResult?: boolean;
}

export interface GenerationInputReference {
  readonly asset_id: string;
  readonly asset_version_id: string;
  readonly asset_content_sha256?: string;
}

export type GenerationBudgetScope = { readonly kind: "episode"; readonly episode_id: number } | { readonly kind: "project_shared" };

export interface GenerationRequest {
  readonly budget_scope?: GenerationBudgetScope;
  readonly workspace_id: string;
  readonly project_id: string;
  readonly media_type: GenerationMediaType;
  readonly capability: string;
  readonly prompt: string;
  readonly parameters: Record<string, unknown>;
  readonly input_asset_ids: readonly string[];
  readonly input_asset_references?: readonly GenerationInputReference[];

}

export interface GenerationFailure {
  readonly code: string;
  readonly retryable: boolean;
}

export interface GenerationTask {
  readonly creator?: CreatorTaskInfo;
  readonly task_id: string;
  readonly request: GenerationRequest;
  readonly status: GenerationTaskStatus;
  readonly attempt: number;
  readonly version: number;
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
  readonly currency: string;
  readonly estimated_minor: number;
  readonly actual_minor: number;
  readonly recorded_at: string;
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
    readonly status: "active" | "settled" | "released";
  } | null;
  readonly progress: {
    readonly nativeExecution?: { readonly status: string } | null;
    readonly modelReservation?: { readonly reserved: boolean; readonly submissionUnconfirmed: boolean; readonly submittedAt: string | null };
    readonly queue_position: number | null;
    readonly latest: {
      readonly percent: number;
      readonly eta_seconds: number | null;
      readonly occurred_at: string;
    } | null;
  };
  readonly requestId: string | null;
}

export interface GenerationTaskFilters {
  readonly group?: ProductionGroup;
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
  readonly budget_scope?: GenerationBudgetScope;
  readonly media_type: GenerationMediaType;
  readonly capability: string;
  readonly prompt: string;
  readonly parameters?: Record<string, unknown>;
  readonly input_asset_ids?: readonly string[];
  readonly input_asset_references?: readonly GenerationInputReference[];

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
