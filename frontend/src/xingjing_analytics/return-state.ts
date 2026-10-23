const fields = new Set(["range", "projectId", "episodeId", "modelId", "memberId", "q", "groupBy", "offset"]);
const storageKey = (actor: string, workspace: string) => `creator-analytics:${actor}:${workspace}:return`;

function cleanQuery(raw: string) {
  return new URLSearchParams([...new URLSearchParams(raw)].filter(([field]) => fields.has(field))).toString();
}

export function rememberAnalytics(actor: string, workspace: string, query: string) {
  try { sessionStorage.setItem(storageKey(actor, workspace), JSON.stringify({ query: cleanQuery(query), scroll: window.scrollY })); }
  catch { /* Navigation still works when session storage is unavailable. */ }
}

export function readAnalyticsReturn(actor: string, workspace: string): { query: string; scroll: number } {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(actor, workspace)) ?? "null") as { query?: unknown; scroll?: unknown } | null;
    return {
      query: typeof value?.query === "string" ? cleanQuery(value.query) : "",
      scroll: typeof value?.scroll === "number" && Number.isFinite(value.scroll) ? Math.max(0, value.scroll) : 0,
    };
  } catch { return { query: "", scroll: 0 }; }
}

export function restoreAnalyticsScroll(top: number) {
  let frame = 0;
  let timeout = 0;
  const interaction = ["wheel", "touchstart", "pointerdown", "keydown"] as const;
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(restore);
  });
  function stop() {
    observer.disconnect(); cancelAnimationFrame(frame); window.clearTimeout(timeout);
    for (const event of interaction) window.removeEventListener(event, stop);
  }
  function restore() { window.scrollTo({ top, behavior: "instant" }); }
  observer.observe(document.getElementById("app-root") ?? document.body);
  for (const event of interaction) window.addEventListener(event, stop, { once: true, passive: true });
  timeout = window.setTimeout(stop, 3000);
  frame = requestAnimationFrame(restore);
  return stop;
}
