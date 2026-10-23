export type VersionKind = "script" | "asset" | "shot" | "storyboard" | "final";
export interface VersionRef { kind: VersionKind; objectId: string; versionId: string; shotId?: string }
export interface VersionNode {
  ref: VersionRef; label: string; versionNumber: number; headRevision: number; current: boolean;
  locked: boolean; immutable: boolean; createdAt: string | null; actorId: string | null;
  summary: string; episodeId: string | null; contentDigest: string;
  source: { module: string; sourceId: string; parentVersionId?: string | null };
  actions: { canSnapshot: boolean; canLock: boolean; canRestore: boolean; reason?: string | null };
}
export interface VersionPage {
  scope: { workspaceId: string; projectId: string; actorId: string };
  permissions: { canSnapshot: boolean; canReport: boolean };
  items: VersionNode[]; kinds: VersionKind[]; total: number; offset: number; limit: number;
  summary: { totalVersions: number; todayAdded: number; restorable: number; conflicts: number | null; unknownTime: number };
}
export interface VersionDetail { node: VersionNode; versions: VersionNode[]; content: unknown; history?: { action: string; actorId: string; createdAt: string }[] }
export interface VersionChange { path: string; kind: "added" | "removed" | "changed"; before?: unknown; after?: unknown }
export interface VersionComparison { baseline: VersionNode; candidate: VersionNode; changes: VersionChange[]; digest?: string }
export interface VersionCheckpoint { id: string; label: string; note: string; createdAt: string; createdBy: string; ref: VersionRef; sourceDigest: string; node?: VersionNode; sourceRevision?: number }
export interface CheckpointPage { items: VersionCheckpoint[]; total: number; offset: number; limit: number }
export interface VersionFilters { kind?: string; q?: string; episodeId?: string; objectId?: string; shotId?: string; state?: string; offset: number; limit: number }
export interface VersionCommand { action: "snapshot" | "lock" | "restore"; ref: VersionRef; expectedRevision: number; sourceDigest: string; note: string; label?: string }
export interface VersionCommandResult { node?: VersionNode; checkpoint?: VersionCheckpoint; result?: unknown }
export interface VersionReport { reportId: string; createdAt: string; createdBy: string; report: VersionComparison }
export interface VersionApi {
  list: (filters: VersionFilters, signal?: AbortSignal) => Promise<VersionPage>;
  detail: (ref: VersionRef, signal?: AbortSignal) => Promise<VersionDetail>;
  compare: (baseline: VersionRef, candidate: VersionRef, signal?: AbortSignal) => Promise<VersionComparison>;
  checkpoints: (offset: number, signal?: AbortSignal) => Promise<CheckpointPage>;
  command: (input: VersionCommand) => Promise<VersionCommandResult>;
  report: (baseline: VersionRef, candidate: VersionRef) => Promise<VersionReport>;
}
export const versionRefKey = (ref: VersionRef) => JSON.stringify([ref.kind, ref.objectId, ref.shotId ?? "", ref.versionId]);
export const sameVersionObject = (a: VersionRef, b: VersionRef) => a.kind === b.kind && a.objectId === b.objectId && (a.shotId ?? "") === (b.shotId ?? "");
