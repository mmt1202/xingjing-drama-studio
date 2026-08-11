export interface ApiMeta {
  readonly requestId?: string;
}

export interface ApiEnvelope<T> {
  readonly data: T;
  readonly meta?: ApiMeta;
}

export interface ApiErrorEnvelope {
  readonly error?: {
    readonly code?: string;
    readonly message?: string;
    readonly details?: Readonly<Record<string, unknown>>;
  };
  readonly meta?: ApiMeta;
}

export interface ComplianceAuthorityState {
  readonly tenant_id?: string;
  readonly current_project_version?: string;
  readonly snapshot_is_immutable?: boolean;
  readonly has_export_permission?: boolean;
  readonly compliance_conclusion?: string;
  readonly compliance_project_version?: string;
  readonly manual_review_status?: string;
  readonly authorizations_complete?: boolean;
  readonly billing_settled?: boolean;
  readonly aigc_marking_satisfied?: boolean;
  readonly commercial_export_enabled?: boolean;
}

export interface ComplianceEvidence {
  readonly projectId: string;
  readonly projectVersion: string;
  readonly target: string;
  readonly state: ComplianceAuthorityState;
  readonly policy: { readonly id: string; readonly version: string };
  readonly authorizationIds: readonly string[];
  readonly reviewedAt: string;
  readonly reviewVersion: number;
}

export interface ComplianceReviewResult {
  readonly manual_review_status: string;
  readonly conclusion: string;
  readonly version: number;
  readonly reason: string;
}

export interface AuthorizationRecordInput {
  readonly authorizationId: string;
  readonly authorizationType: string;
  readonly subjectId: string;
  readonly evidenceObjectKey: string;
  readonly evidenceSha256: string;
  readonly validFrom: string;
  readonly validUntil?: string;
}

export interface DeliveryRecord {
  readonly tenant_id: string;
  readonly project_id: string;
  readonly project_version: string;
  readonly request_id: string;
  readonly policy_version: string;
  readonly manifest_digest: string;
  readonly created_at: string;
  readonly target: string | null;
  readonly status: string;
  readonly format: string | null;
  readonly object_key: string | null;
  readonly content_sha256: string | null;
  readonly size_bytes: number | null;
  readonly download_path: string | null;
}

export interface ExportPreflight {
  readonly allowed: boolean;
  readonly blockCodes: readonly string[];
  readonly manifest: Readonly<Record<string, unknown>> | null;
  readonly isDelivery: false;
}

export interface ComplianceScope {
  readonly projectId: string;
  readonly projectVersion: string;
  readonly target: string;
}

export interface ExportContextOption {
  readonly projectVersion: string;
  readonly timelineId: string;
  readonly timelineVersionId: string;
  readonly createdAt: string;
}

export type FormalExportFormat =
  | "mp4"
  | "subtitle_srt"
  | "storyboard_csv"
  | "davinci_edl"
  | "premiere_xml"
  | "compliance_report"
  | "cost_report"
  | "material_package"
  | "project_archive"
  | "jianying_draft"
  | "publish_package";

export interface ComplianceExportPort {
  listExportContexts(
    projectId: string,
    signal?: AbortSignal,
  ): Promise<readonly ExportContextOption[]>;
  getCompliance(
    scope: ComplianceScope,
    signal?: AbortSignal,
  ): Promise<ComplianceEvidence>;
  listDeliveries(
    projectId: string,
    signal?: AbortSignal,
  ): Promise<readonly DeliveryRecord[]>;
  preflightExport(
    scope: ComplianceScope,
    signal?: AbortSignal,
  ): Promise<ExportPreflight>;
  applyReviewAction(
    scope: ComplianceScope,
    input: {
      readonly action: "appeal" | "approve" | "reject";
      readonly reason: string;
      readonly expectedVersion: number;
    },
    signal?: AbortSignal,
  ): Promise<ComplianceReviewResult>;
  submitExport(
    scope: ComplianceScope,
    outputFormat: FormalExportFormat,
    signal?: AbortSignal,
  ): Promise<DeliveryRecord>;
  recordAuthorization(
    scope: ComplianceScope,
    input: AuthorizationRecordInput,
    signal?: AbortSignal,
  ): Promise<Readonly<Record<string, unknown>>>;
  downloadDelivery(
    projectId: string,
    requestId: string,
    signal?: AbortSignal,
  ): Promise<Blob>;
  runComplianceCheck(
    scope: ComplianceScope,
    signal?: AbortSignal,
  ): Promise<Readonly<Record<string, unknown>>>;
}
