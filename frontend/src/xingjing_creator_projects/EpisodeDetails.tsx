import { useEffect, useState } from "react";
import { episodeSourceLabel, type Episode, type EpisodeSourceText, type EpisodesSnapshot } from "./episodes-api";
import { useEpisodesTranslation } from "./episodes-i18n";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";

export const episodeInput = "mt-1 w-full rounded-lg border border-[#363B49] bg-[#0C0E14] px-3 py-2 text-sm text-slate-100 outline-none focus:border-[#FF6B4A] disabled:opacity-60";
export const episodeButton = "rounded-lg border border-[#363B49] px-3 py-2 text-sm transition hover:border-[#FF6B4A] focus-visible:outline-2 focus-visible:outline-[#FF6B4A] disabled:cursor-not-allowed disabled:opacity-40";

export function EpisodeDetails({ episode, sources, disabled, update, readSource, canReadSources }: {
  episode: Episode; sources: EpisodesSnapshot["sources"]; disabled: boolean; update: (patch: Partial<Episode>) => void;
  readSource: (sourceId: string, signal: AbortSignal) => Promise<EpisodeSourceText>;
  canReadSources: boolean;
}) {
  const { t } = useEpisodesTranslation();
  const { t: handoff } = useEpisodeHandoffTranslation();
  const [expanded, setExpanded] = useState(false), [loaded, setLoaded] = useState<EpisodeSourceText>(), [failed, setFailed] = useState(false);
  const source = sources.find((doc) => doc.path === episode.sourceRange?.source_file);
  const range = episode.sourceRange;
  useEffect(() => {
    if (!expanded || !source) return;
    const abort = new AbortController();
    void readSource(source.path, abort.signal).then((value) => {
      if (!abort.signal.aborted) { if (value.digest !== source.digest || value.path !== source.path) throw new Error("SOURCE_MISMATCH"); setLoaded(value); setFailed(false); }
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => abort.abort();
  }, [expanded, source, readSource]);
  const excerpt = loaded && loaded.path === source?.path && loaded.digest === source.digest && range ? Array.from(loaded.text).slice(range.start, Math.min(range.end, range.start + 6000)).join("") : "";
  return <aside className="rounded-xl border border-[#2A2E3A] bg-[#12141B] p-5">
    <p className="text-xs uppercase tracking-widest text-[#FF9B83]">{t("details")} / {String(episode.episode).padStart(2, "0")}</p>
    {episode.locked && <p className="mt-3 rounded-lg bg-amber-950/40 p-3 text-xs leading-5 text-amber-200">{t("locked")}</p>}
    <fieldset disabled={disabled} className="mt-4 space-y-4">
      <label className="block text-sm text-slate-300">{t("title")}<input className={episodeInput} maxLength={200} value={episode.title} disabled={episode.locked} onChange={(e) => update({ title: e.target.value })} /></label>
      <label className="block text-sm text-slate-300">{t("hook")}<textarea className={episodeInput} rows={3} maxLength={4000} value={episode.hook} onChange={(e) => update({ hook: e.target.value })} /></label>
      <label className="block text-sm text-slate-300">{t("beats")}<textarea className={episodeInput} rows={4} value={episode.storyBeats.join("\n")} onChange={(e) => update({ storyBeats: e.target.value.split("\n") })} /></label>
      <label className="block text-sm text-slate-300">{t("teaser")}<textarea className={episodeInput} rows={2} maxLength={4000} value={episode.nextEpisodeTeaser} onChange={(e) => update({ nextEpisodeTeaser: e.target.value })} /></label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-sm text-slate-300">{t("duration")}<input className={episodeInput} type="number" min={1} max={3600} value={episode.targetDuration} onChange={(e) => update({ targetDuration: Number(e.target.value) })} /></label>
        <label className="block text-sm text-slate-300">{t("budget")}<input className={episodeInput} type="number" min={0} max={10000000} step="0.01" value={episode.budgetMinor === null ? "" : episode.budgetMinor / 100} placeholder={t("unknown")} onChange={(e) => update({ budgetMinor: e.target.value === "" ? null : Math.round(Number(e.target.value) * 100) })} /></label>
      </div>
      <fieldset disabled={episode.locked || !canReadSources} className="space-y-3 border-t border-[#2A2E3A] pt-4">
        <label className="block text-sm text-slate-300">{t("source")}<select className={episodeInput} value={range?.source_file ?? ""} onChange={(e) => update({ sourceRange: e.target.value ? { source_file: e.target.value, start: 0, end: 0 } : null })}>
          <option value="">{t("noRange")}</option>{sources.map((doc) => <option key={doc.path} value={doc.path}>{episodeSourceLabel(doc, sources)}</option>)}
        </select></label>
        {range && <><div className="grid grid-cols-2 gap-3">
          <label className="block text-sm text-slate-300">{t("start")}<input className={episodeInput} type="number" min={0} value={range.start} onChange={(e) => update({ sourceRange: { ...range, start: Number(e.target.value) } })} /></label>
          <label className="block text-sm text-slate-300">{t("end")}<input className={episodeInput} type="number" min={range.start + 1} max={source?.length} value={range.end} onChange={(e) => update({ sourceRange: { ...range, end: Number(e.target.value) } })} /></label>
        </div><p className="text-xs leading-5 text-slate-400">{t("rangeHelp")}</p></>}
      </fieldset>
    </fieldset>
    {range && source ? <details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)} className="mt-4 border-t border-[#2A2E3A] pt-3"><summary className="cursor-pointer text-sm text-[#FF9B83]">{t("sourcePreview")}</summary>{failed ? <p role="alert" className="mt-3 text-xs text-amber-200">{handoff("sourceFailed")}</p> : excerpt ? <p className="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-slate-300">{excerpt}{range.end - range.start > 6000 ? "…" : ""}</p> : <p className="mt-3 text-xs text-slate-400">{handoff("sourceLoading")}</p>}</details> : null}
  </aside>;
}
