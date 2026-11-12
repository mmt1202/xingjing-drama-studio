import { useEffect, useState } from "react";
import type { Candidate, Plan, SourcePlanApi } from "./api";
import { button, card, field, primary, useSourcePlanTranslation } from "./i18n";
import { SourceSnapshotReader } from "./SourceSnapshotReader";

export function validCandidates(plan: Plan, candidates: Candidate[]) {
  const ids = new Set(plan.proposal.candidates.map(item => item.id));
  if (!candidates.length || new Set(candidates.map(item => item.id)).size !== candidates.length) return false;
  if (!candidates.every(item => ids.has(item.id) && item.title.trim() && item.title.length <= 200
    && Number.isSafeInteger(item.targetDuration) && item.targetDuration >= 1 && item.targetDuration <= 3600
    && Number.isSafeInteger(item.start) && Number.isSafeInteger(item.end) && item.end > item.start
    && plan.proposal.availableRanges.some(([start, end]) => start <= item.start && item.end <= end))) return false;
  const ordered = [...candidates].sort((left, right) => left.start - right.start);
  return ordered.every((item, index) => index === 0 || item.start >= ordered[index - 1].end);
}

interface Props {
  plan: Plan; api: SourcePlanApi; candidates: Candidate[]; included: string[]; busy: boolean; blocked: boolean; modified: boolean;
  canManage: boolean; pending: boolean; outdated: boolean;
  onReviewChange: (value: boolean) => void;
  onChange: (candidates: Candidate[], included: string[]) => void; onConfirm: (candidates: Candidate[]) => void;
  onReset: () => void; onClose: () => void; onNavigate: (target: string) => void; onDenied: (error: unknown) => void;
}
export function CandidateEditor(props: Props) {
  const { t } = useSourcePlanTranslation();
  const { plan, candidates, included, api } = props;
  const [confirming, setConfirming] = useState(false), [inspected, setInspected] = useState<string>();
  const [expanded, setExpanded] = useState<string | undefined>(candidates[0]?.id);
  const { onReviewChange } = props;
  useEffect(() => { onReviewChange(confirming); return () => onReviewChange(false); }, [confirming, onReviewChange]);
  const accepted = candidates.filter(item => included.includes(item.id));
  const valid = validCandidates(plan, accepted);
  const disabled = props.busy || props.blocked || props.pending || props.outdated || !props.canManage || !!plan.result || plan.confirmationPending;
  const selected = accepted.reduce((sum, item) => sum + Math.max(0, item.end - item.start), 0);
  const inspectedItem = candidates.find(item => item.id === inspected);
  function update(item: Candidate, patch: Partial<Candidate>) {
    setConfirming(false);
    props.onChange(candidates.map(candidate => candidate.id === item.id ? { ...candidate, ...patch } : candidate), included);
  }
  return <section className={card + " mt-5"}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-lg font-semibold">{t("planTitle")}</h3><p className="mt-2 text-xs text-violet-200">{plan.source.label} · {t("rules")}</p></div><button className={button} disabled={props.busy || props.pending || props.modified || confirming} onClick={props.onClose}>{t("close")}</button></div>
    {plan.result ? <div role="status" className="mt-5 rounded-xl border border-emerald-300/20 bg-emerald-500/5 p-4"><p className="text-sm leading-7 text-emerald-100">{t("done")}</p><ul className="mt-4 space-y-3">{plan.result.items.map(item => <li key={item.scriptId} className="flex flex-wrap items-center justify-between gap-3"><span className="text-sm">{t("episode", { number: item.episode })} · {item.title}</span><button className={button} disabled={props.busy || props.pending} onClick={() => props.onNavigate(`script-lab?${new URLSearchParams({ episodeId: String(item.episode), scriptId: item.scriptId })}`)}>{t("openScript")}</button></li>)}</ul></div> : <>
      <p className="mt-4 text-sm leading-7 text-slate-400">{t("editHelp")}</p>
      {!props.canManage && <p className="mt-3 text-sm text-amber-200">{t("readonly")}</p>}
      {props.outdated && <p role="status" className="mt-3 text-sm text-amber-200">{t("sourceChanged")}</p>}
      {plan.confirmationPending && !props.pending && <p role="status" className="mt-3 text-sm text-amber-200">{t("pendingOther")}</p>}
      <div className="mt-5 space-y-4">{plan.proposal.candidates.map(proposal => {
        const item = candidates.find(item => item.id === proposal.id) ?? proposal;
        const chosen = included.includes(item.id);
        return <article className="rounded-xl border border-white/10 p-4" key={item.id}>
          <label className="flex items-center gap-3 text-sm font-medium"><input type="checkbox" checked={chosen} aria-label={t("include", { number: proposal.episode })} disabled={disabled || confirming} onChange={event => { setConfirming(false); props.onChange(candidates, event.target.checked ? [...included, item.id] : included.filter(id => id !== item.id)); }} />{t("episode", { number: proposal.episode })}</label>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><span className="min-w-0 break-words text-sm">{item.title} · {t("durationValue", { count: item.targetDuration })}</span><button className={button} aria-expanded={expanded === item.id} onClick={() => setExpanded(expanded === item.id ? undefined : item.id)}>{t(expanded === item.id ? "collapse" : "editCandidate")}</button></div>
          {expanded === item.id && <><div className="mt-3 grid gap-3 sm:grid-cols-[1fr_180px]"><label className="text-xs text-slate-400">{t("titleField")}<input className={field} value={item.title} maxLength={200} disabled={disabled || confirming || !chosen} onChange={event => update(item, { title: event.target.value })} /></label><label className="text-xs text-slate-400">{t("duration")}<input className={field} type="number" min={1} max={3600} value={item.targetDuration} disabled={disabled || confirming || !chosen} onChange={event => update(item, { targetDuration: Number(event.target.value) })} /></label></div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">{(["start", "end"] as const).map(boundary => <div key={boundary} className="grid grid-cols-[1fr_130px] gap-2"><label className="text-xs text-slate-400">{t(boundary === "start" ? "fromChapter" : "toChapter")}<select className={field} value={plan.chapters.find(chapter => chapter[boundary] === item[boundary])?.id ?? ""} disabled={disabled || confirming || !chosen} onChange={event => { const chapter = plan.chapters.find(chapter => chapter.id === event.target.value); if (chapter) update(item, { [boundary]: chapter[boundary] }); }}><option value="">{t("customRange")}</option>{plan.chapters.map(chapter => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title ?? t(chapter.kind)}</option>)}</select></label><label className="text-xs text-slate-400">{t(boundary)}<input className={field} type="number" min={boundary === "start" ? 0 : 1} max={plan.source.length} value={item[boundary]} disabled={disabled || confirming || !chosen} onChange={event => update(item, { [boundary]: Number(event.target.value) })} /></label></div>)}</div>
          </>}
          <p className="mt-3 text-xs leading-6 text-slate-500">{plan.chapters.filter(chapter => chapter.start < item.end && chapter.end > item.start).map(chapter => `${chapter.ordinal}. ${chapter.title ?? t(chapter.kind)}`).join(" / ")}</p>
          <button className={button + " mt-3"} disabled={item.start < 0 || item.end <= item.start || item.end > plan.source.length} onClick={() => setInspected(item.id)}>{t("inspectSource")}</button>
        </article>;
      })}</div>
      <p className="mt-3 text-xs leading-6 text-slate-500">{t("coordinates")}</p>
      <p className="mt-4 text-sm text-slate-300">{t("remaining", { selected, remaining: Math.max(0, plan.proposal.uncoveredCharacters - selected) })}</p>
      {!valid && <p role="status" className="mt-3 text-sm text-amber-200">{t("invalid")}</p>}
      {props.modified && <p role="status" className="mt-3 text-xs text-violet-200">{t("savedDraft")}</p>}
      {confirming ? <div className="mt-5 rounded-xl border border-violet-300/30 p-4"><p className="text-sm leading-7">{t("confirmHelp", { count: accepted.length })}</p><div className="mt-4 flex flex-wrap gap-3"><button className={primary} disabled={disabled || !valid} onClick={() => { setConfirming(false); props.onConfirm(accepted); }}>{t(props.busy ? "working" : "confirm")}</button><button className={button} disabled={props.busy} onClick={() => setConfirming(false)}>{t("cancel")}</button></div></div> : <div className="mt-5 flex flex-wrap gap-3"><button className={primary} disabled={disabled || !valid} onClick={() => setConfirming(true)}>{t("review")}</button><button className={button} disabled={props.busy || props.pending || !props.modified} onClick={props.onReset}>{t("reset")}</button></div>}
    </>}
    {inspectedItem && inspectedItem.start >= 0 && inspectedItem.end > inspectedItem.start && inspectedItem.end <= plan.source.length && <SourceSnapshotReader key={`${plan.snapshotId}:${inspectedItem.start}:${inspectedItem.end}`} api={api} projectId={plan.projectId} reference={{ sourceId: plan.source.path, digest: plan.source.digest, start: inspectedItem.start, end: inspectedItem.end, snapshotId: plan.snapshotId }} onDenied={props.onDenied} />}
  </section>;
}
