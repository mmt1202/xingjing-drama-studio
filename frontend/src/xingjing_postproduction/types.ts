export interface ProjectScope {
  workspaceId: string;
  projectId: string;
  episodeId?: string;
}

export type TaskStatus = "queued" | "processing" | "succeeded" | "failed" | "cancelled" | "blocked";
export type ComplianceStatus = "pending" | "processing" | "passed" | "blocked" | "appealed" | "expired";

export interface TimelineClip {
  id: string;
  sourceId: string;
  startMs: number;
  endMs: number;
  volume?: number;
  effect?: string;
}

export interface TimelineTrack {
  id: string;
  kind: "video" | "voice" | "subtitle" | "bgm" | "sfx" | "watermark" | "review_marker";
  clips: TimelineClip[];
}

export interface FinalVideoVersion {
  id: string;
  label: string;
  version: number;
  status: TaskStatus;
  mediaUrl?: string;
  checksum?: string;
  createdAt: string;
}

export interface Timeline {
  id: string;
  version: number;
  updatedAt: string;
  tracks: TimelineTrack[];
  finalVersions: FinalVideoVersion[];
  watermark?: { platform: boolean; customerText?: string; aigcLabel: boolean };
}

export interface ComplianceRecord {
  id: string;
  version: number;
  ruleVersion: string;
  projectVersion: number;
  status: ComplianceStatus;
  riskLevel: "low" | "medium" | "high" | "critical";
  evidence: Array<{ id: string; type: string; name: string; expiresAt?: string }>;
  owner?: string;
  unblockCondition?: string;
  updatedAt: string;
}

export interface ExportTask {
  id: string;
  version: number;
  target: string;
  format: string;
  projectVersion: number;
  complianceStatus: ComplianceStatus;
  status: TaskStatus;
  downloadUrl?: string;
  downloadExpiresAt?: string;
  manifestChecksum?: string;
  updatedAt: string;
}

export interface Page<T> { items: T[]; nextCursor?: string; total: number }
export interface SessionContext { userId: string; workspaceId: string; permissions: string[]; featureFlags: string[] }

export interface TaskReceipt { id: string; status: TaskStatus; version: number; requestId?: string }

