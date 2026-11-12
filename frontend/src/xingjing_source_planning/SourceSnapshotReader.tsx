import { useEffect, useState } from "react";
import type { SnapshotExcerpt, SourcePlanApi, SourceReference } from "./api";
import { button, card, problemText, useSourcePlanTranslation } from "./i18n";

export function SourceSnapshotReader({ api, projectId, reference, onDenied }: { api: SourcePlanApi; projectId: string; reference: SourceReference; onDenied?: (error: unknown) => void }) {
  const { t } = useSourcePlanTranslation();
  const [offset, setOffset] = useState(reference.start), [reload, setReload] = useState(0);
  const [state, setState] = useState<{ key: string; data?: SnapshotExcerpt; error?: unknown }>();
  const start = Math.max(reference.start, Math.min(offset, Math.max(reference.start, reference.end - 1))), end = Math.min(reference.end, start + 4000);
  const key = JSON.stringify([projectId, reference.snapshotId, start, end, reload]);
  useEffect(() => {
    if (!reference.snapshotId) return;
    const controller = new AbortController();
    api.snapshot(projectId, reference.snapshotId, start, end, controller.signal).then(data => { if (!controller.signal.aborted) setState({ key, data }); })
      .catch(error => { if (!controller.signal.aborted) { setState({ key, error }); onDenied?.(error); } });
    return () => controller.abort();
  }, [api, projectId, reference.snapshotId, start, end, key, onDenied]);
  if (!reference.snapshotId) return <p className="text-sm text-slate-400">{t("legacySource")}</p>;
  const current = state?.key === key ? state : undefined;
  return <section className={card + " mt-4"}>
    <div className="flex flex-wrap items-center justify-between gap-3"><h4 className="font-medium">{t("snapshot")}</h4><span className="text-xs tabular-nums text-slate-500">{start}–{end} / {reference.end}</span></div>
    <p className="mt-2 text-xs leading-6 text-slate-400">{t("snapshotHelp")}</p>
    {!current ? <p role="status" className="mt-4 text-sm text-slate-400">{t("loading")}</p> : current.error ? <div role="alert" className="mt-4 text-sm text-amber-200"><p>{problemText(current.error, t)}</p><button className={button + " mt-3"} onClick={() => setReload(value => value + 1)}>{t("refresh")}</button></div> : <>
      <p className="mt-3 text-xs text-violet-200">{current.data?.chapters.map(chapter => `${chapter.ordinal}. ${chapter.title ?? t(chapter.kind)}`).join(" / ")}</p>
      <pre className="mt-4 max-h-96 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-8 text-slate-200">{current.data?.text}</pre>
    </>}
    <div className="mt-4 flex gap-2"><button className={button} disabled={start === reference.start} onClick={() => setOffset(Math.max(reference.start, start - 4000))}>{t("previousText")}</button><button className={button} disabled={end === reference.end} onClick={() => setOffset(end)}>{t("nextText")}</button></div>
  </section>;
}
