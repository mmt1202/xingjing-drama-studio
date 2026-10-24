import { useEffect, useState } from "react";
import { episodeSourceLabel, type EpisodeSource, type EpisodeSourceText, type EpisodesApi } from "./episodes-api";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";
import { episodeButton as button, episodeInput as input } from "./EpisodeDetails";

export function EpisodeSourcePicker({ api, projectId, sources, value, disabled, onChange }: {
  api: EpisodesApi; projectId: string; sources: EpisodeSource[]; value: string; disabled: boolean; onChange: (value: string) => void;
}) {
  const { t } = useEpisodeHandoffTranslation();
  const [loaded, setLoaded] = useState<EpisodeSourceText>(), [error, setError] = useState(false), [reading, setReading] = useState(false), [retry, setRetry] = useState(0);
  const selected = sources.find((source) => source.path === value);
  useEffect(() => {
    if (!selected) return;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      setReading(true); setError(false);
      void api.source(projectId, selected.path, abort.signal).then((source) => {
        if (abort.signal.aborted) return;
        if (source.path !== selected.path || source.digest !== selected.digest || Array.from(source.text).length !== source.length) throw new Error("SOURCE_MISMATCH");
        setLoaded(source);
      }).catch(() => { if (!abort.signal.aborted) setError(true); }).finally(() => { if (!abort.signal.aborted) setReading(false); });
    }, 0);
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [api, projectId, selected, retry]);
  const text = loaded?.path === value && loaded.digest === selected?.digest ? loaded.text : undefined;
  return <div className="mt-4 space-y-3"><label className="block text-xs text-slate-400">{t("chooseSource")}<select className={input} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}><option value="">{t("chooseSource")}</option>{value && !selected ? <option value={value}>{t("sourceUnavailable")}</option> : null}{sources.map((source) => <option key={source.path} value={source.path}>{episodeSourceLabel(source, sources)} · {t(source.kind === "sql_original" ? "sqlOriginal" : "fileOriginal")} · {t("sourceLength", { count: source.length })}</option>)}</select></label><p className="text-xs leading-6 text-slate-500">{t("sourceHelp")}</p>{selected ? <p className="break-all font-mono text-[10px] text-slate-500">{selected.scriptId ?? selected.path} · {selected.digest}</p> : null}{reading ? <p role="status" className="text-xs text-slate-400">{t("sourceLoading")}</p> : null}{error ? <div role="alert" className="text-xs text-amber-200"><p>{t("sourceFailed")}</p><button type="button" className={button + " mt-2"} onClick={() => setRetry((previous) => previous + 1)}>{t("retry")}</button></div> : null}{text !== undefined ? <details><summary className="cursor-pointer text-xs text-violet-200">{t("sourcePreview")} · {t("sourceLength", { count: selected?.length })}</summary><p className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-7 text-slate-400">{Array.from(text).slice(0, 6000).join("")}{Array.from(text).length > 6000 ? "…" : ""}</p></details> : null}</div>;
}
