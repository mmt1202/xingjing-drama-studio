import type { Chapter } from "./api";

export interface Attribution {
  target_id: string; target_digest: string; source_id: string; source_digest: string; snapshot_id: string | null;
  source_start: number; source_end: number; chapter_ids: string[]; kind: "exact_quote" | "manual_adaptation";
  note: string; confirmed_by: string; confirmed_at: string;
}
export interface MappingInput { targetId: string; start: number; end: number; kind: Attribution["kind"]; note: string }
export interface MappingCommand { revision: number; version: number; sourceDigest: string; mappings: MappingInput[] }
export interface MappingResult { scriptId: string; revision: number; version: number; count: number; confirmedAt: string }
export interface MappingView {
  scriptId: string; revision: number; version: number; canManage: boolean;
  source: { id: string; digest: string; snapshotId: string | null; label: string; start: number; end: number };
  chapters: Chapter[]; excerpt: { start: number; end: number; text: string };
  matches: { start: number; end: number }[]; moreMatches: boolean; mappings: Attribution[];
}
export const mappingInput = (item: Attribution): MappingInput => ({ targetId: item.target_id, start: item.source_start, end: item.source_end, kind: item.kind, note: item.note });
export interface MappingDraft { command: MappingCommand; pendingKey?: string }
export function readMappingDraft(key: string): MappingDraft | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 4_000_000) return;
    const draft = JSON.parse(raw) as MappingDraft, value = draft?.command;
    if (!value || !Number.isSafeInteger(value.revision) || !Number.isSafeInteger(value.version)
      || value.revision < 1 || value.version < 1 || !/^[a-f0-9]{64}$/.test(value.sourceDigest)
      || draft.pendingKey !== undefined && !/^[a-z0-9-]{1,128}$/i.test(draft.pendingKey)
      || !Array.isArray(value.mappings) || value.mappings.length > 2000 || value.mappings.some(item =>
        !item || typeof item.targetId !== "string" || item.targetId.length > 128
        || !Number.isSafeInteger(item.start) || !Number.isSafeInteger(item.end)
        || !["exact_quote", "manual_adaptation"].includes(item.kind) || typeof item.note !== "string" || item.note.length > 1000)) return;
    return draft;
  } catch { return; }
}
