import type { ControlEpisode } from "./control-api";

const scriptRoutes = new Set(["script-import", "script-lab", "source", "rewrite", "versions", "source-mapping", "script-checkup", "ai-director", "director-assets"]);

export function productionNavigationQuery({ path, currentPath, currentQuery, targetQuery, episodes, episodeOverride, inheritContext = true, targetPlatform }: {
  path: string; currentPath: string; currentQuery: URLSearchParams; targetQuery: URLSearchParams;
  episodes: ControlEpisode[]; episodeOverride?: number | null; inheritContext?: boolean; targetPlatform: string;
}) {
  const query = new URLSearchParams(targetQuery);
  const currentEpisode = currentQuery.get("episodeId");
  const selectedId = episodeOverride !== undefined
    ? episodeOverride === null ? null : String(episodeOverride)
    : query.has("episodeId") ? query.get("episodeId") : inheritContext ? currentEpisode : null;
  const selected = episodes.find((episode) => String(episode.episode) === selectedId);
  if (episodeOverride === null) query.delete("episodeId");
  else if (selected) query.set("episodeId", String(selected.episode));

  // A script chosen within the same episode remains selected between script pages.
  const currentScriptId = currentQuery.get("scriptId");
  if (inheritContext && episodeOverride === undefined && selectedId === currentEpisode && currentScriptId
    && scriptRoutes.has(path) && scriptRoutes.has(currentPath) && !query.has("scriptId")) {
    query.set("scriptId", currentScriptId);
  }
  if (!selected) return query;
  if (scriptRoutes.has(path) && !query.has("scriptId") && selected.scriptId) query.set("scriptId", selected.scriptId);
  if (path.startsWith("assets") || path.startsWith("shots") || path === "storyboard") {
    if (!query.has("scriptVersionId") && selected.scriptVersionId) query.set("scriptVersionId", selected.scriptVersionId);
    if (!query.has("assetSnapshotId") && selected.assetSnapshotId) query.set("assetSnapshotId", selected.assetSnapshotId);
  }
  if (/^(release|export|cover|compliance|rights|provenance|aigc)/.test(path) && selected.finalVersionId) {
    if (!query.has("projectVersion")) query.set("projectVersion", selected.finalVersionId);
    if (!query.has("target")) query.set("target", targetPlatform);
  }
  if (path === "postproduction" && !query.has("timelineId") && selected.timelineId) query.set("timelineId", selected.timelineId);
  return query;
}
