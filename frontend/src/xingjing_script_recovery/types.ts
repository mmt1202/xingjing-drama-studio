import type { ScriptVersion } from "@/xingjing_creator_projects/types";

export type Part = "scene" | "paragraph" | "heading";
export type Action = "replace" | "insert" | "remove";
export interface Change { action: Action; part: Part; sourceId: string | null; targetId: string }
export interface Selection { revision: number; baseVersion: number; sourceVersion: number; changes: Change[]; reason: string }
export interface Confirmation { selection: Selection; previewDigest: string }
export interface Overview {
  scriptId: string; revision: number; baseVersion: number; mode: "fork" | "append"; episode: number | null; canConfirm: boolean;
}
export interface Preview extends Overview {
  version: number; sourceVersion: number; previewDigest: string; changes: (Change & { before: string; after: string })[];
  candidate: string; mappings: { kept: number; restored: number; superseded: number };
  validation: { code: string; path: string }[]; workingSequence: number | null; previousWorkingScriptId: string | null;
  impact: { scriptVersionId: string; directorCount: number; refreshAnalysis: boolean; directors: { version: number; confirmed: boolean }[];
    assets: number | null; boards: { count: number; items: { id: string; episode: string; version: number }[] } | null };
}
export interface Result {
  mode: "fork" | "append"; scriptId: string; parentScriptId: string; version: number; revision: number; episode: number | null; confirmedAt: string;
}
export interface Draft { selection: Selection; pending?: { key: string; digest: string } }
export interface Item { id: string; kind: string; label: string; body: string }

export function objects(version: ScriptVersion | undefined, part: Part, insertAnchor = false): Item[] {
  return (version?.scenes ?? []).flatMap(scene => {
    const heading = [scene.heading.location, scene.heading.timeOfDay, scene.heading.setting].filter(Boolean).join("｜");
    const sceneItem = { id: scene.sceneId, kind: "scene", label: heading,
      body: part === "scene" ? [heading, ...scene.paragraphs.map(p => p.sourceText)].join("\n") : heading };
    if (part !== "paragraph") return [sceneItem];
    const paragraphs = scene.paragraphs.map(p => ({ id: p.paragraphId, kind: p.kind, label: p.sourceText, body: p.sourceText }));
    return insertAnchor ? [sceneItem, ...paragraphs] : paragraphs;
  });
}

export function readRecoveryDraft(key: string): Draft | undefined {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return;
    const value = JSON.parse(raw) as Draft;
    const selection = value?.selection;
    if (!selection || ![selection.revision, selection.baseVersion, selection.sourceVersion].every(n => Number.isSafeInteger(n) && n > 0)
      || typeof selection.reason !== "string" || selection.reason.length > 1000
      || !Array.isArray(selection.changes) || selection.changes.length > 100) return;
    if (!selection.changes.every(c => c && ["replace", "insert", "remove"].includes(c.action) && ["scene", "heading", "paragraph"].includes(c.part)
      && typeof c.targetId === "string" && c.targetId.length <= 128 && (c.sourceId === null || typeof c.sourceId === "string" && c.sourceId.length <= 128))) return;
    if (value.pending && (!/^[a-zA-Z0-9-]{1,128}$/.test(value.pending.key) || !/^[a-f0-9]{64}$/.test(value.pending.digest))) return;
    return value;
  } catch { return; }
}
