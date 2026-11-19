export const AI_CHARACTER_LIMIT = 20_000;

export function isSourceFilePath(text: string): boolean {
  const value = text.trim();
  return value.length <= 4096 && /^(?:[a-z]:[\\/]|\\\\[^\\/]+[\\/]|file:\/+|\/|~\/|\.\.?[\\/])[^\r\n]+\.(?:txt|md|markdown|docx?|pdf|epub|rtf)$/i.test(value.replace(/^["'“”]|["'“”]$/g, ""));
}

export function narrationDraft(source: string, heading: string, prefix: string) {
  const paragraphs = source.split(/\r\n|[\n\r\u2028\u2029]/u).filter(line => line.trim());
  return [heading, ...paragraphs.map(line => prefix + line)].join("\n");
}
