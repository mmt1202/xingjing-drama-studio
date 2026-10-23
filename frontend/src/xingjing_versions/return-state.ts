const fields = new Set(["kind", "q", "episodeId", "objectId", "shotId", "state", "offset", "versionId", "baselineVersionId", "candidateVersionId", "checkpointOffset"]);
const storageKey = (actor: string, workspace: string, project: string) => `creator-versions:${actor}:${workspace}:${project}:return`;
function cleanQuery(raw: string) { return new URLSearchParams([...new URLSearchParams(raw)].filter(([field]) => fields.has(field))).toString(); }
export function rememberVersionReturn(actor: string, workspace: string, project: string, query: string) {
  try { sessionStorage.setItem(storageKey(actor, workspace, project), JSON.stringify({ query: cleanQuery(query), scroll: window.scrollY })); }
  catch { /* Navigation remains available when session storage is disabled. */ }
}
export function readVersionReturn(actor: string, workspace: string, project: string): { query: string; scroll: number; present: boolean } {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(actor, workspace, project)) ?? "null") as { query?: unknown; scroll?: unknown } | null;
    return { query: typeof value?.query === "string" ? cleanQuery(value.query) : "", scroll: typeof value?.scroll === "number" && Number.isFinite(value.scroll) ? Math.max(0, value.scroll) : 0, present: typeof value?.query === "string" };
  } catch { return { query: "", scroll: 0, present: false }; }
}
export function restoreVersionScroll(top: number) {
  let frame = 0;
  const observer = new ResizeObserver(() => { cancelAnimationFrame(frame); frame = requestAnimationFrame(restore); });
  const interactions = ["wheel", "touchstart", "pointerdown", "keydown"] as const;
  let timeout = 0;
  function restore() { window.scrollTo({ top, behavior: "instant" }); }
  function stop() {
    observer.disconnect(); cancelAnimationFrame(frame); window.clearTimeout(timeout);
    for (const event of interactions) window.removeEventListener(event, stop);
  }
  observer.observe(document.getElementById("app-root") ?? document.body);
  for (const event of interactions) window.addEventListener(event, stop, { once: true, passive: true });
  timeout = window.setTimeout(stop, 3000); frame = requestAnimationFrame(restore);
  return stop;
}
