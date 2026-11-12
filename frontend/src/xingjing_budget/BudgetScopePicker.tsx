import { useEffect, useMemo, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { createAuthenticatedFetcher, getTokenForSession } from "@/utils/auth";
import type { BudgetOptions, BudgetScope } from "./contracts";
import { useBudgetTranslation } from "./i18n";

export type { BudgetScope } from "./contracts";
function parseOptions(value: unknown): BudgetOptions {
  if (!value || typeof value !== "object" || !("episodes" in value) || !Array.isArray(value.episodes)
    || !("canUseSharedScope" in value) || typeof value.canUseSharedScope !== "boolean"
    || !("requiresAttribution" in value) || typeof value.requiresAttribution !== "boolean") throw new Error();
  const rows: unknown[] = value.episodes;
  const episodes = rows.map((episode) => {
    if (!episode || typeof episode !== "object" || !("episodeId" in episode) || typeof episode.episodeId !== "number"
      || !Number.isSafeInteger(episode.episodeId) || episode.episodeId < 1 || !("title" in episode) || typeof episode.title !== "string") throw new Error();
    return { episodeId: episode.episodeId, title: episode.title };
  });
  return { episodes, canUseSharedScope: value.canUseSharedScope, requiresAttribution: value.requiresAttribution };
}
export function BudgetScopePicker({ projectId, workspaceId, value, onChange, disabled = false, onReady }: {
  projectId: string; workspaceId?: string; value: BudgetScope | undefined;
  onChange: (value: BudgetScope | undefined) => void; disabled?: boolean; onReady?: (ready: boolean) => void;
}) {
  const { t, i18n } = useBudgetTranslation();
  const epoch = useAuthStore((state) => state.sessionEpoch);
  const fetcher = useMemo(() => {
    const bound = createAuthenticatedFetcher({ workspaceId });
    return (input: RequestInfo | URL, init?: RequestInit) => { getTokenForSession(epoch); return bound(input, init); };
  }, [workspaceId, epoch]);
  const [result, setResult] = useState<{ key: string; options?: BudgetOptions; failed?: boolean }>();
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([epoch, workspaceId, projectId, retry]);
  const current = result?.key === key ? result : undefined;
  useEffect(() => {
    const abort = new AbortController();
    void fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/budget/options`, {
      signal: abort.signal, headers: { Accept: "application/json", "Accept-Language": i18n.resolvedLanguage ?? "zh" },
    }).then(async (response) => {
      if (!response.ok) throw new Error();
      const envelope: unknown = await response.json();
      const data = parseOptions(envelope && typeof envelope === "object" && "data" in envelope ? envelope.data : undefined);
      if (!abort.signal.aborted) setResult({ key, options: data });
    }).catch(() => { if (!abort.signal.aborted) setResult({ key, failed: true }); });
    return () => abort.abort();
  }, [fetcher, projectId, key, i18n.resolvedLanguage]);
  const options = current?.options;
  const valid = Boolean(options && value && (value.kind === "project_shared" ? options.canUseSharedScope : options.episodes.some((episode) => episode.episodeId === value.episodeId)));
  useEffect(() => { onReady?.(valid); }, [valid, onReady]);
  const selected = value?.kind === "project_shared" ? "shared" : value ? String(value.episodeId) : "";
  return <section className="rounded-xl border border-white/10 p-4" aria-label={t("title")}>
    <label className="block text-sm">{t("title")}<select className="mt-2 w-full rounded-lg border border-white/10 bg-[#0b0d12] px-3 py-2 text-sm" value={selected} disabled={disabled || !options} onChange={(event) => onChange(event.target.value === "shared" ? { kind: "project_shared" } : event.target.value ? { kind: "episode", episodeId: Number(event.target.value) } : undefined)}>
      <option value="">{t("choose")}</option>
      {value && !valid && options ? <option value={selected} disabled>{t("choose")}</option> : null}
      {options?.canUseSharedScope ? <option value="shared">{t("shared")}</option> : null}
      {options?.episodes.map((episode) => <option key={episode.episodeId} value={episode.episodeId}>{t("episode", { number: episode.episodeId, title: episode.title })}</option>)}
    </select></label>
    <p className="mt-2 text-xs leading-6 text-slate-400">{t("help")} {t("immutable")}</p>
    {!current ? <p role="status" className="mt-2 text-xs text-slate-400">{t("loading")}</p> : current.failed ? <div role="alert" className="mt-2 text-xs text-amber-200"><p>{t("failed")}</p><button type="button" className="mt-2 rounded border border-white/10 px-3 py-2" disabled={disabled} onClick={() => setRetry((count) => count + 1)}>{t("retry")}</button></div> : options && !options.episodes.length && !options.canUseSharedScope ? <p role="status" className="mt-2 text-xs text-amber-200">{t("empty")}</p> : null}
  </section>;
}
