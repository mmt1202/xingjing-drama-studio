export type Locale = "zh" | "en" | "vi";

export interface WorkspaceProjectScope {
  readonly workspaceId: string;
  readonly projectId?: string;
  readonly episodeId?: string;
}

export interface SessionContext {
  readonly user: { readonly id: string; readonly name: string };
  readonly workspace: { readonly id: string; readonly name: string };
  readonly project?: {
    readonly id: string;
    readonly name: string;
    readonly version: number;
    readonly status: string;
  };
  readonly episode?: { readonly id: string; readonly title: string };
  readonly permissions: readonly string[];
  readonly featureFlags: Readonly<Record<string, boolean>>;
}

export interface PageQuery {
  readonly pageToken?: string;
  readonly pageSize?: number;
  readonly sort?: string;
  readonly search?: string;
}

export interface PageResult<T> {
  readonly items: readonly T[];
  readonly nextToken: string | null;
  readonly collectionVersion?: number;
  readonly requestId?: string;
}

export type AssetType = "character" | "scene" | "prop" | "costume" | "voice";
export type RightsStatus =
  "missing" | "pending" | "verified" | "expired" | "blocked";
export type WorkStatus =
  | "draft"
  | "not_started"
  | "queued"
  | "running"
  | "retrying"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "timeout"
  | "frozen";

export interface AssetVersion {
  readonly id: string;
  readonly versionNo: number;
  readonly label?: string;
  readonly status: WorkStatus;
  readonly source: string;
  readonly mediaUrl?: string;
  readonly thumbnailUrl?: string;
  readonly createdAt: string;
  readonly createdBy?: string;
}

export interface RightsRecord {
  readonly id?: string;
  readonly holder?: string;
  readonly licenseScope?: string;
  readonly proofUploadId?: string;
  readonly validFrom?: string;
  readonly validTo?: string;
  readonly status: RightsStatus;
}

export interface AssetItem {
  readonly id: string;
  readonly projectId?: string;
  readonly type: AssetType;
  readonly name: string;
  readonly tags: readonly string[];
  readonly source: string;
  readonly status: WorkStatus;
  readonly rightsStatus: RightsStatus;
  readonly currentVersionId?: string;
  readonly version: number;
  readonly referenceCount: number;
  readonly updatedAt: string;
  readonly thumbnailUrl?: string;
  readonly description?: string;
  readonly metadata: Readonly<Record<string, unknown>>;
  readonly versions: readonly AssetVersion[];
  readonly rights?: RightsRecord;
  readonly affectedShotIds?: readonly string[];
}

export interface PromptVersion {
  readonly id: string;
  readonly versionNo: number;
  readonly type: "image" | "video" | "director" | "consistency" | "negative";
  readonly text: string;
  readonly negativeText?: string;
  readonly variables: Readonly<Record<string, string>>;
  readonly modelVersion?: string;
}

export interface StoryboardCandidate {
  readonly id: string;
  readonly version: number;
  readonly mediaUrl?: string;
  readonly thumbnailUrl?: string;
  readonly selected: boolean;
  readonly status: WorkStatus;
}

export interface ShotVersion {
  readonly id: string;
  readonly versionNo: number;
  readonly shotSize: string;
  readonly cameraMove: string;
  readonly durationMs: number;
  readonly dialogue?: string;
  readonly prompt?: string;
  readonly status: WorkStatus;
  readonly createdAt: string;
}

export interface QualityIssue {
  readonly id: string;
  readonly code: string;
  readonly severity: "info" | "warning" | "blocking";
  readonly message: string;
  readonly retryable?: boolean;
  readonly recoveryAction?: string;
}

export interface ShotItem {
  readonly id: string;
  readonly storyboardId: string;
  readonly episodeId?: string;
  readonly shotNo: string;
  readonly sequenceNo: number;
  readonly version: number;
  readonly status: WorkStatus;
  readonly shotSize: string;
  readonly cameraMove: string;
  readonly durationMs: number;
  readonly dialogue?: string;
  readonly prompt?: string;
  readonly negativePrompt?: string;
  readonly promptTemplateId?: string;
  readonly promptVariables?: Readonly<Record<string, string>>;
  readonly promptModelAdapterVersion?: string;
  readonly assetReferences: readonly {
    readonly id: string;
    readonly type: AssetType;
    readonly name: string;
  }[];
  readonly generationStatus?: WorkStatus;
  readonly generationTaskId?: string;
  readonly generationFailure?: string;
  readonly generationCurrency?: string;
  readonly estimatedCostMinor?: number;
  readonly actualCostMinor?: number;
  readonly billingStatus?: string;
  readonly estimatedCredit?: number;
  readonly actualCredit?: number;
  readonly issueCount: number;
  readonly qualityIssues: readonly QualityIssue[];
  readonly candidates: readonly StoryboardCandidate[];
  readonly versions: readonly ShotVersion[];
  readonly prompts?: readonly PromptVersion[];
  readonly updatedAt: string;
}

export type BatchItemStatus = "succeeded" | "failed" | "cancelled" | "skipped";

export interface BatchItemResult {
  readonly id: string;
  readonly status: BatchItemStatus;
  readonly message?: string;
  readonly code?: string;
  readonly retryable?: boolean;
  readonly version?: number;
}

export interface BatchReceipt {
  readonly operationId: string;
  readonly items: readonly BatchItemResult[];
  readonly succeeded: number;
  readonly failed: number;
  readonly requestId?: string;
}

export interface TaskReceipt {
  readonly taskId: string;
  readonly projectId?: string;
  readonly status: WorkStatus;
  readonly progress?: number;
  readonly statusUrl?: string;
  readonly requestId?: string;
  readonly downloadUrl?: string;
  readonly downloadExpiresAt?: string;
  readonly outputAssetIds?: readonly string[];
  readonly outputUrls?: readonly string[];
  readonly failureMessage?: string;
  readonly billingCurrency?: string;
  readonly estimatedCostMinor?: number;
  readonly actualCostMinor?: number;
  readonly billingStatus?: string;
}

export interface PromptTemplateItem {
  readonly id: string;
  readonly name: string;
  readonly mediaType: "image" | "video" | "both";
  readonly template: string;
  readonly negativePrompt: string;
  readonly variables: readonly string[];
  readonly modelAdapterVersions: readonly string[];
  readonly version: number;
  readonly usageCount: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface PromptTemplateInput {
  readonly name: string;
  readonly mediaType: "image" | "video" | "both";
  readonly template: string;
  readonly negativePrompt: string;
  readonly variables: readonly string[];
  readonly modelAdapterVersions: readonly string[];
}

export interface AssetActionResult {
  readonly asset?: AssetItem;
  readonly task?: TaskReceipt;
  readonly batch?: BatchReceipt;
  readonly message?: string;
  readonly requestId?: string;
}

export interface ShotActionResult {
  readonly shot?: ShotItem;
  readonly shots?: readonly ShotItem[];
  readonly task?: TaskReceipt;
  readonly batch?: BatchReceipt;
  readonly issues?: readonly QualityIssue[];
  readonly message?: string;
  readonly requestId?: string;
}

export type AssetAction =
  | "extractAssets"
  | "createAsset"
  | "updateAsset"
  | "createVersion"
  | "setPrimaryVersion"
  | "restoreVersion"
  | "saveRights"
  | "bindReference"
  | "generateTurnaround"
  | "generateExpressions"
  | "adoptGenerationOutput"
  | "licenseMarketAsset"
  | "confirmAssets"
  | "batchUpdate"
  | "retryFailedItems";

export type ShotAction =
  | "generateShots"
  | "updateShot"
  | "batchUpdate"
  | "reorder"
  | "splitShot"
  | "mergeShots"
  | "runQualityCheck"
  | "replaceShot"
  | "preflightImport"
  | "commitImport"
  | "createExport"
  | "savePrompt"
  | "confirmStoryboard"
  | "selectStoryboardCandidate"
  | "restoreVersion"
  | "retryFailedItems"
  | "generateShotMedia"
  | "adoptShotMedia";

export interface ActionInput<TAction extends string> {
  readonly action: TAction;
  readonly targetId?: string;
  readonly version?: number;
  readonly payload?: Readonly<Record<string, unknown>>;
}

export interface AssetListQuery extends PageQuery {
  readonly assetType?: AssetType;
  readonly rightsStatus?: RightsStatus;
  readonly status?: WorkStatus;
  readonly marketItemId?: string;
}

export interface ShotListQuery extends PageQuery {
  readonly status?: WorkStatus;
  readonly hasIssues?: boolean;
}

export interface UploadReceipt {
  readonly uploadId: string;
  readonly sha256: string;
  readonly fileName: string;
}

export interface RightsEvidenceUpload {
  readonly objectKey: string;
  readonly sha256: string;
  readonly fileName: string;
  readonly mediaType: string;
}

export interface AssetsStoryboardPort {
  getSessionContext(
    scope: WorkspaceProjectScope,
    signal?: AbortSignal,
  ): Promise<SessionContext>;
  listAssets(
    scope: WorkspaceProjectScope,
    query?: AssetListQuery,
    signal?: AbortSignal,
  ): Promise<PageResult<AssetItem>>;
  listMarketAssets(
    scope: WorkspaceProjectScope,
    query?: AssetListQuery,
    signal?: AbortSignal,
  ): Promise<PageResult<AssetItem>>;
  listShots(
    scope: WorkspaceProjectScope,
    query?: ShotListQuery,
    signal?: AbortSignal,
  ): Promise<PageResult<ShotItem>>;
  listPromptTemplates(
    scope: WorkspaceProjectScope,
    signal?: AbortSignal,
  ): Promise<readonly PromptTemplateItem[]>;
  createPromptTemplate(
    scope: WorkspaceProjectScope,
    input: PromptTemplateInput,
    signal?: AbortSignal,
  ): Promise<PromptTemplateItem>;
  updatePromptTemplate(
    scope: WorkspaceProjectScope,
    promptId: string,
    version: number,
    input: PromptTemplateInput,
    signal?: AbortSignal,
  ): Promise<PromptTemplateItem>;
  archivePromptTemplate(
    scope: WorkspaceProjectScope,
    promptId: string,
    version: number,
    signal?: AbortSignal,
  ): Promise<void>;
  assetAction(
    scope: WorkspaceProjectScope,
    input: ActionInput<AssetAction>,
    signal?: AbortSignal,
  ): Promise<AssetActionResult>;
  getGenerationTask(
    scope: WorkspaceProjectScope,
    task: TaskReceipt,
    signal?: AbortSignal,
  ): Promise<TaskReceipt>;
  uploadRightsEvidence(
    scope: WorkspaceProjectScope,
    asset: AssetItem,
    file: File,
    signal?: AbortSignal,
  ): Promise<RightsEvidenceUpload>;
  shotAction(
    scope: WorkspaceProjectScope,
    input: ActionInput<ShotAction>,
    signal?: AbortSignal,
  ): Promise<ShotActionResult>;
  uploadImportFile(
    scope: WorkspaceProjectScope,
    file: File,
    signal?: AbortSignal,
  ): Promise<UploadReceipt>;
}

export function moveStableItem<T extends { readonly id: string }>(
  items: readonly T[],
  id: string,
  delta: -1 | 1,
): T[] {
  const currentIndex = items.findIndex((item) => item.id === id);
  if (currentIndex < 0) return [...items];
  const nextIndex = Math.max(
    0,
    Math.min(items.length - 1, currentIndex + delta),
  );
  if (nextIndex === currentIndex) return [...items];
  const next = [...items];
  const [moved] = next.splice(currentIndex, 1);
  if (moved) next.splice(nextIndex, 0, moved);
  return next;
}

export function failedBatchItemIds(
  items: readonly BatchItemResult[],
): string[] {
  return items
    .filter((item) => item.status !== "succeeded" && item.retryable === true)
    .map((item) => item.id);
}
