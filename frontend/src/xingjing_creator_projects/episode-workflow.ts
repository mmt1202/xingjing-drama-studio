import { emptyProduction, type Episode, type EpisodeSource, type ProductionCatalog } from "./episodes-api";

export const episodeStages = ["script", "assets", "shots", "film", "delivery"] as const;
export type EpisodeStage = typeof episodeStages[number];

export function episodeMaterials(episode: Episode, items: Episode[], catalog: ProductionCatalog) {
  const binding = episode.production ?? emptyProduction();
  const record = catalog.items.find(item => item.episode === episode.episode);
  const script = catalog.scripts.find(item => item.id === binding.scriptVersionId);
  const unavailableScripts = new Set<string>();
  for (const item of items) {
    if (item.episode !== episode.episode && item.production?.scriptVersionId) unavailableScripts.add(item.production.scriptVersionId);
  }
  const unavailableDocuments = new Set(items.flatMap(item =>
    item.workingScript && (item.episode !== episode.episode || !item.workingScript.rangeMatches) ? [item.workingScript.scriptId] : [],
  ));
  const scripts = catalog.scripts.filter(item => !unavailableScripts.has(item.id) && !unavailableDocuments.has(item.id.slice(0, item.id.lastIndexOf("@"))));
  const snapshots = catalog.snapshots.filter(item => script && item.valid && item.assets.length && item.scriptVersionId === script.id && item.digest === script.digest);
  const finals = catalog.finals.filter(item => item.episodeId === String(episode.episode));
  const working = episode.workingScript;
  return {
    binding, record, script, scripts, snapshots, finals,
    workingFinal: working?.rangeMatches && working.lockedVersionNumber ? scripts.find(item => item.id === `${working.scriptId}@${working.lockedVersionNumber}`) : undefined,
    assets: snapshots.find(item => item.id === binding.assetSnapshotId),
    final: finals.find(item => item.id === binding.finalVideoVersionId),
    shots: record?.boards.reduce((count, board) => count + board.shots, 0) ?? 0,
    release: record?.deliveries.find(item => item.format === "publish_package" && item.status === "succeeded"),
  };
}

export type EpisodeMaterials = ReturnType<typeof episodeMaterials>;
type NextAction = "importSource" | "chooseSource" | "createDraft" | "replaceDraft" | "parse" | "finalize" | "useScript"
  | "fixScript" | "extract" | "useAssets" | "fixAssets" | "planShots" | "makeShots" | "referenceVideo"
  | "editFilm" | "useFilm" | "fixFilm" | "review" | "deliver" | "openPackage" | "lockedInputs" | "restricted";
interface NextStep { stage: EpisodeStage; action: NextAction; permission?: string }

export function episodeNextStep(
  episode: Episode, materials: EpisodeMaterials, permissions: string[], sources: EpisodeSource[], referenceVideo: boolean,
): NextStep {
  const { binding, record, workingFinal, script, assets, final, finals, snapshots, shots } = materials;
  const has = (permission: string) => permissions.includes(permission);
  const issue = record?.issue;
  if (binding.finalVideoVersionId && has("final.view") && !final) return { stage: "delivery", action: "fixFilm", permission: "project.manage" };
  if (final) {
    if (record?.state === "package_ready") return { stage: "delivery", action: "openPackage", permission: "export.view" };
    if (record?.state === "pending_review" || record?.state === "review_blocked") return { stage: "delivery", action: "review", permission: "compliance.view" };
    return { stage: "delivery", action: "deliver", permission: "export.view" };
  }
  if (finals.length) return { stage: "delivery", action: "useFilm", permission: "project.manage" };
  if (episode.videos.total > 0 && episode.videos.completed >= episode.videos.total) return { stage: "film", action: "editFilm", permission: "final.view" };
  if (shots) return { stage: "film", action: referenceVideo ? "referenceVideo" : "makeShots", permission: referenceVideo ? "generation.view" : "shot.view" };
  if (!has("script.view")) return { stage: "script", action: "restricted" };
  if (binding.scriptVersionId && !script) return { stage: "script", action: "fixScript", permission: "project.manage" };
  if (script) {
    if (!has("asset.view")) return { stage: "assets", action: "restricted" };
    if (issue === "EPISODE_ASSET_SNAPSHOT_MISMATCH" || (binding.assetSnapshotId && !assets)) return { stage: "assets", action: "fixAssets", permission: "project.manage" };
    if (!assets) return snapshots.length ? { stage: "assets", action: "useAssets", permission: "project.manage" } : { stage: "assets", action: "extract", permission: "asset.manage" };
    return referenceVideo ? { stage: "film", action: "referenceVideo", permission: "generation.view" } : { stage: "shots", action: "planShots", permission: "shot.view" };
  }
  if (workingFinal) return { stage: "script", action: "useScript", permission: "project.manage" };
  if (episode.workingScript?.rangeMatches) return { stage: "script", action: episode.workingScript.validationState === "invalid" ? "parse" : "finalize", permission: "script.view" };
  if (episode.locked || record?.boards.length) return { stage: "script", action: "lockedInputs" };
  if (!sources.length) return { stage: "script", action: "importSource", permission: "script.manage" };
  const range = episode.sourceRange;
  const source = sources.find(item => item.path === range?.source_file);
  if (!source || !range || range.start < 0 || range.end <= range.start || range.end > source.length || episode.issues.includes("source_changed")) return { stage: "script", action: "chooseSource", permission: "project.manage" };
  return { stage: "script", action: episode.workingScript ? "replaceDraft" : "createDraft", permission: "script.manage" };
}
