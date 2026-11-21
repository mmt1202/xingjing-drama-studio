import type { AssetsStoryboardPort, WorkspaceProjectScope } from "@/xingjing_assets_storyboard/contracts";
import type { SubtitleLine } from "./contracts";

export interface CaptionRow {
  id: string;
  start: string;
  end: string;
  text: string;
  sourceLineId?: string;
}

export function editLine(line: SubtitleLine): CaptionRow {
  return {
    id: line.id,
    start: String(line.startMs / 1000),
    end: String(line.endMs / 1000),
    text: line.text,
    sourceLineId: line.sourceLineId,
  };
}

export function newLine(after = 0): CaptionRow {
  return editLine({ id: crypto.randomUUID(), startMs: after, endMs: after + 3000, text: "" });
}

export function readLines(rows: CaptionRow[]) {
  const lines: SubtitleLine[] = [];
  for (const [index, row] of rows.entries()) {
    const startMs = Math.round(Number(row.start) * 1000);
    const endMs = Math.round(Number(row.end) * 1000);
    const fail = (key: "invalidTime" | "overlap" | "emptyText") =>
      ({ issue: { key, index }, lines: undefined }) as const;
    if (!row.text.trim()) return fail("emptyText");
    if (
      !row.start.trim() ||
      !row.end.trim() ||
      !Number.isSafeInteger(startMs) ||
      !Number.isSafeInteger(endMs) ||
      startMs < 0 ||
      endMs <= startMs
    )
      return fail("invalidTime");
    if (lines.length && startMs < lines[lines.length - 1].endMs) return fail("overlap");
    lines.push({
      id: row.id,
      startMs,
      endMs,
      text: row.text.trim(),
      ...(row.sourceLineId && { sourceLineId: row.sourceLineId }),
    });
  }
  return { lines, issue: undefined } as const;
}

export async function readShotCaptions(
  api: Pick<AssetsStoryboardPort, "listShots">,
  scope: WorkspaceProjectScope,
  signal: AbortSignal,
) {
  if (!scope.episodeId) throw new Error("episodeRequired");
  const query = { browseStoryboard: true, pageSize: 100 };
  const first = await api.listShots(scope, query, signal);
  const board = first.shotList?.boardId;
  const shots = [...first.items];
  const tokens = new Set<string>();
  let token = first.nextToken;
  while (token) {
    if (!board || tokens.has(token)) throw new Error("sourceChanged");
    tokens.add(token);
    const next = await api.listShots(scope, { ...query, storyboardId: board, pageToken: token }, signal);
    if (
      !next.items.length ||
      next.collectionVersion !== first.collectionVersion ||
      next.shotList?.boardId !== board
    )
      throw new Error("sourceChanged");
    shots.push(...next.items);
    token = next.nextToken;
  }
  const rows: CaptionRow[] = [];
  let elapsed = 0;
  for (const shot of shots) {
    if (
      shot.episodeId !== scope.episodeId ||
      (board && shot.storyboardId !== board) ||
      !Number.isSafeInteger(shot.durationMs) ||
      shot.durationMs < 1
    )
      throw new Error("sourceChanged");
    const text = shot.dialogue?.trim();
    if (text)
      rows.push(
        editLine({
          id: crypto.randomUUID(),
          startMs: elapsed,
          endMs: elapsed + shot.durationMs,
          text,
          sourceLineId: shot.id,
        }),
      );
    elapsed += shot.durationMs;
  }
  return { rows, durationMs: elapsed };
}

export function downloadSrt(lines: SubtitleLine[], title: string) {
  const time = (ms: number) => {
    const hours = String(Math.floor(ms / 3600000)).padStart(2, "0");
    const minutes = String(Math.floor(ms / 60000) % 60).padStart(2, "0");
    const seconds = String(Math.floor(ms / 1000) % 60).padStart(2, "0");
    return hours + ":" + minutes + ":" + seconds + "," + String(ms % 1000).padStart(3, "0");
  };
  const contents = lines
    .map((line, i) => [i + 1, time(line.startMs) + " --> " + time(line.endMs), line.text].join("\r\n"))
    .join("\r\n\r\n");
  const url = URL.createObjectURL(
    new Blob(["\uFEFF", contents, "\r\n"], { type: "application/x-subrip;charset=utf-8" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = (title.replace(/[<>:"/\\|?*]/g, "_").trim() || "subtitles") + ".srt";
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
