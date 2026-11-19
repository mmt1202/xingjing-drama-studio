import type { ScriptDocument, ScriptRecognition } from "./types";

export const AI_CHARACTER_LIMIT = 20_000;

export function matchesScriptRecognition(info: ScriptRecognition | undefined, script: ScriptDocument): info is ScriptRecognition {
  return Boolean(info && info.scriptId === script.id && info.revision === script.revision
    && info.version === script.versions.at(-1)?.number && ["screenplay", "chapters", "prose", "path"].includes(info.kind)
    && [info.characterCount, info.sceneCount, info.chapterCount, info.paragraphCount].every(n => Number.isSafeInteger(n) && n >= 0)
    && Array.isArray(info.chapterTitles));
}

export function isSourceFilePath(text: string): boolean {
  const value = text.trim();
  return value.length <= 4096 && /^(?:[a-z]:[\\/]|\\\\[^\\/]+[\\/]|file:\/+|\/|~\/|\.\.?[\\/])[^\r\n]+\.(?:txt|md|markdown|docx?|pdf|epub|rtf)$/i.test(value.replace(/^["'“”]|["'“”]$/g, ""));
}

export function narrationDraft(source: string, heading: string, prefix: string) {
  const paragraphs = source.split(/\r\n|[\n\r\u2028\u2029]/u).filter(line => line.trim());
  return [heading, ...paragraphs.map(line => prefix + line)].join("\n");
}
