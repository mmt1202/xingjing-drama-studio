import type { Episode, EpisodesSnapshot } from "./episodes-api";
import { useEpisodesTranslation } from "./episodes-i18n";

export const episodeInput = "mt-1 w-full rounded-lg border border-[#363B49] bg-[#0C0E14] px-3 py-2 text-sm text-slate-100 outline-none focus:border-[#FF6B4A] disabled:opacity-60";
export const episodeButton = "rounded-lg border border-[#363B49] px-3 py-2 text-sm transition hover:border-[#FF6B4A] focus-visible:outline-2 focus-visible:outline-[#FF6B4A] disabled:cursor-not-allowed disabled:opacity-40";

export function EpisodeDetails({ episode, sources, disabled, update }: {
  episode: Episode; sources: EpisodesSnapshot["sources"]; disabled: boolean; update: (patch: Partial<Episode>) => void;
}) {
  const { t } = useEpisodesTranslation();
  const source = sources.find((doc) => doc.path === episode.sourceRange?.source_file);
  const range = episode.sourceRange;
  const excerpt = source && range ? Array.from(source.text).slice(range.start, range.end).join("") : "";
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
      <fieldset disabled={episode.locked} className="space-y-3 border-t border-[#2A2E3A] pt-4">
        <label className="block text-sm text-slate-300">{t("source")}<select className={episodeInput} value={range?.source_file ?? ""} onChange={(e) => update({ sourceRange: e.target.value ? { source_file: e.target.value, start: 0, end: 0 } : null })}>
          <option value="">{t("noRange")}</option>{sources.map((doc) => <option key={doc.path} value={doc.path}>{doc.path}</option>)}
        </select></label>
        {range && <><div className="grid grid-cols-2 gap-3">
          <label className="block text-sm text-slate-300">{t("start")}<input className={episodeInput} type="number" min={0} value={range.start} onChange={(e) => update({ sourceRange: { ...range, start: Number(e.target.value) } })} /></label>
          <label className="block text-sm text-slate-300">{t("end")}<input className={episodeInput} type="number" min={range.start + 1} max={source ? Array.from(source.text).length : undefined} value={range.end} onChange={(e) => update({ sourceRange: { ...range, end: Number(e.target.value) } })} /></label>
        </div><p className="text-xs leading-5 text-slate-400">{t("rangeHelp")}</p></>}
      </fieldset>
    </fieldset>
    {excerpt && <details className="mt-4 border-t border-[#2A2E3A] pt-3"><summary className="cursor-pointer text-sm text-[#FF9B83]">{t("sourcePreview")}</summary><p className="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-slate-300">{Array.from(excerpt).slice(0, 6000).join("")}{Array.from(excerpt).length > 6000 ? "…" : ""}</p></details>}
  </aside>;
}
