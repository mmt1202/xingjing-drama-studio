import { useEffect, useMemo, useRef, useState } from "react";
import { CreatorApiError } from "@/xingjing_creator_projects/api";
import type { ScriptFrameContext } from "@/xingjing_creator_projects/script-workflow";
import { createSourcePlanApi, denied, uncertain } from "./api";
import { button, card, field, primary } from "./i18n";
import { useMappingTranslation } from "./mapping-i18n";
import { mappingInput, readMappingDraft, type MappingDraft, type MappingInput, type MappingView } from "./mapping-types";
import { storageKey } from "./storage";

export function ScriptSourceMapper({ frame, projectId, onBlocked }: {
  frame: ScriptFrameContext; projectId: string; onBlocked: (value: boolean) => void;
}) {
  const { t } = useMappingTranslation(), script = frame.script!;
  const api = useMemo(() => createSourcePlanApi(frame.context.workspace.id), [frame.context.workspace.id]);
  const storage = "mapping:" + storageKey(frame.context.workspace.id, frame.context.user.id, projectId) + ":" + script.id;
  const [draft, setDraft] = useState<MappingDraft | undefined>(() => readMappingDraft(storage));
  const [versionNumber, setVersionNumber] = useState(draft?.command.version ?? script.versions.at(-1)!.number);
  const [offset, setOffset] = useState<number>(), [find, setFind] = useState(""), [query, setQuery] = useState("");
  const [reload, setReload] = useState(0), [filter, setFilter] = useState("all"), [targetId, setTargetId] = useState("");
  const [range, setRange] = useState<{ start: number; end: number }>(), [kind, setKind] = useState<MappingInput["kind"]>("manual_adaptation"), [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false), [discard, setDiscard] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(), [localError, setLocalError] = useState(""), [saved, setSaved] = useState<number>();
  const [forbidden, setForbidden] = useState(false), [view, setView] = useState<{ key: string; value: MappingView }>();
  const submitting = useRef(false), alive = useRef(true), accessEpoch = useRef(0);
  const key = JSON.stringify([versionNumber, offset, query, reload, script.revision]);
  const data = view?.key === key ? view.value : undefined;
  const version = script.versions.find(item => item.number === versionNumber);
  const objects = useMemo(() => (version?.scenes ?? []).flatMap(scene => [
    { id: scene.sceneId, label: scene.heading.location ?? scene.sceneId, kind: "scene", text: scene.paragraphs.map(p => p.sourceText).join("\n") },
    ...scene.paragraphs.map(p => ({ id: p.paragraphId, label: p.speaker ? `${p.speaker}: ${p.text}` : p.text, kind: p.kind, text: p.text })),
  ]), [version]);
  const inputs = draft?.command.mappings ?? data?.mappings.map(mappingInput) ?? [];
  const mapped = new Map(inputs.map(item => [item.targetId, item]));
  const filtered = objects.filter(item => filter === "all" || (filter === "mapped") === mapped.has(item.id));
  const target = filtered.find(item => item.id === targetId) ?? filtered[0];
  const existing = target ? mapped.get(target.id) : undefined;
  const evidence = data?.mappings.find(item => item.target_id === target?.id);
  const conflict = Boolean(draft && data && (draft.command.revision !== data.revision || draft.command.version !== data.version || draft.command.sourceDigest !== data.source.digest));
  const pending = Boolean(draft?.pendingKey), locked = busy || pending || confirm || conflict || !frame.canManage || !data?.canManage;
  const hasEdit = Boolean(draft), blocked = hasEdit || busy || confirm;
  useEffect(() => { onBlocked(blocked); return () => onBlocked(false); }, [blocked, onBlocked]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const abort = new AbortController();
    const epoch = accessEpoch.current;
    api.mapping(projectId, script.id, versionNumber, offset, query, abort.signal).then(value => {
      if (!abort.signal.aborted && epoch === accessEpoch.current) { setView({ key, value }); setForbidden(false); setError(undefined); }
    }).catch((failure: unknown) => {
      if (!abort.signal.aborted && epoch === accessEpoch.current) { setView(undefined); setError(failure); if (denied(failure)) { accessEpoch.current++; setForbidden(true); } }
    });
    return () => abort.abort();
  }, [api, projectId, script.id, versionNumber, offset, query, key]);
  const persist = (value: MappingDraft | undefined): boolean => {
    try { if (value) sessionStorage.setItem(storage, JSON.stringify(value)); else sessionStorage.removeItem(storage); }
    catch { setLocalError(t("storage")); return false; }
    setDraft(value); setLocalError(""); setSaved(undefined); return true;
  };
  const edit = (next: MappingInput[]) => {
    if (locked || !data) return;
    if (next.length > 2000) { setLocalError(t("limit")); return; }
    persist({ command: { revision: data.revision, version: data.version, sourceDigest: data.source.digest, mappings: next } });
  };
  const apply = () => {
    if (!data || !target || !range || !Number.isSafeInteger(range.start) || !Number.isSafeInteger(range.end)
      || range.start < data.source.start || range.end > data.source.end || range.end <= range.start || target.kind === "scene" && kind === "exact_quote") {
      setLocalError(t("invalid")); return;
    }
    edit([...inputs.filter(item => item.targetId !== target.id), { targetId: target.id, ...range, kind, note: note.trim() }]);
  };
  const submit = async () => {
    if (submitting.current || !draft || !frame.canManage || !data || forbidden || (!pending && (!data.canManage || conflict))) return;
    const operation = { ...draft, pendingKey: draft.pendingKey ?? crypto.randomUUID() };
    if (!persist(operation)) return;
    submitting.current = true; setBusy(true); setConfirm(false); setError(undefined);
    try {
      const result = await api.confirmMapping(projectId, script.id, operation.command, operation.pendingKey);
      if (!alive.current) return;
      if (result.scriptId !== script.id || !Number.isSafeInteger(result.version)) throw new Error("Invalid mapping result");
      if (persist(undefined)) {
        setSaved(result.version); setVersionNumber(result.version); setRange(undefined); setNote(""); setOffset(undefined); setQuery("");
        frame.reload(); setReload(value => value + 1);
      }
    } catch (failure) {
      if (!alive.current) return;
      setError(failure);
      if (denied(failure)) { accessEpoch.current++; setForbidden(true); setView(undefined); }
      if (!uncertain(failure)) persist({ command: operation.command });
    } finally { submitting.current = false; if (alive.current) setBusy(false); }
  };
  const problem = error instanceof CreatorApiError ? (denied(error) ? t("permission") : error.message) : t("network");
  const status = <>
    {error ? <p role="alert" className="mt-3 text-sm text-amber-200">{problem}</p> : null}
    {localError ? <p role="alert" className="mt-3 text-sm text-amber-200">{localError}</p> : null}
    <button className={button + " mt-3"} disabled={busy} onClick={() => setReload(value => value + 1)}>{t("refresh")}</button>
  </>;
  if (forbidden) return <section className={card}>{status}</section>;
  return <section className={card}>
    <h2 className="text-lg font-semibold">{t("title")}</h2><p className="mt-2 text-sm leading-7 text-slate-400">{t("intro")}</p>
    <label className="mt-4 block max-w-md text-sm">{t("version")}<select className={field} value={versionNumber} disabled={blocked} onChange={e => { setVersionNumber(Number(e.target.value)); setTargetId(""); setOffset(undefined); setQuery(""); setRange(undefined); setNote(""); setSaved(undefined); }}>{script.versions.map(item => <option key={item.number} value={item.number}>v{item.number} · {item.changeSummary}</option>)}</select></label>
    {saved ? <p role="status" className="mt-4 text-sm text-emerald-200">{t("saved", { version: saved })}</p> : null}
    {draft ? <p role="status" className="mt-4 text-sm text-amber-200">{t(pending ? "pending" : "dirty")}</p> : null}
    {conflict ? <p role="alert" className="mt-3 text-sm text-amber-200">{t("conflict")}</p> : null}
    {status}
    {!data ? <p role="status" className="mt-5 text-sm text-slate-400">{error ? "" : t("loading")}</p> : <>
      {!data.canManage ? <p className="mt-4 text-sm text-violet-200">{t("frozen")}</p> : null}
      <p className="mt-5 text-sm text-violet-200">{t("progress", { mapped: mapped.size, total: objects.length })}</p>
      {!objects.length ? <div className="mt-4"><p className="text-sm text-slate-400">{t("noTargets")}</p><button className={button + " mt-3"} disabled={blocked || !frame.canNavigate} onClick={() => frame.navigate("script-lab")}>{t("openLab")}</button></div> : null}
      <div className={"mt-5 grid gap-6" + (objects.length ? " xl:grid-cols-2" : "")}>
        {objects.length ? <section className="min-w-0 space-y-4">
          <label className="block text-sm">{t("filter")}<select className={field} value={filter} disabled={busy || confirm} onChange={e => { setFilter(e.target.value); setTargetId(""); setRange(undefined); setNote(""); }}>{["all", "mapped", "unmapped"].map(value => <option key={value} value={value}>{t(value)}</option>)}</select></label>
          <label className="block text-sm">{t("targets")}<select className={field} disabled={!filtered.length || busy || confirm} value={target?.id ?? ""} onChange={e => { setTargetId(e.target.value); setRange(undefined); setNote(""); }}>{filtered.map(item => <option key={item.id} value={item.id}>{item.id} · {t(item.kind)} · {item.label.slice(0, 90)}</option>)}</select></label>
          {target ? <><h3 className="text-sm text-slate-400">{t("currentText")}</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-7">{target.text}</pre>
            <button className={button} disabled={busy || !target.text || target.kind === "scene"} onClick={() => { const value = Array.from(target.text).slice(0, 500).join(""); setFind(value); setQuery(value); }}>{t("sameText")}</button>
            {existing ? <div className="rounded-xl border border-violet-400/20 p-4 text-sm"><p>{t(existing.kind)} · {existing.start}–{existing.end}</p>{existing.note ? <p className="mt-2 whitespace-pre-wrap text-slate-400">{existing.note}</p> : null}{evidence && !draft ? <p className="mt-2 break-all text-xs text-slate-500">{t("confirmed", { actor: evidence.confirmed_by, time: evidence.confirmed_at })}</p> : null}<div className="mt-3 flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={() => { setOffset(existing.start); setRange({ start: existing.start, end: existing.end }); setKind(existing.kind); setNote(existing.note); }}>{t("viewMapped")}</button><button className={button} disabled={locked} onClick={() => edit(inputs.filter(item => item.targetId !== target.id))}>{t("remove")}</button></div></div> : <p className="text-sm text-amber-200">{t("unmapped")}</p>}
          </> : <p className="text-sm text-slate-400">{t("emptyFilter")}</p>}
          <div className="grid grid-cols-2 gap-3">{(["start", "end"] as const).map(name => <label key={name} className="block text-sm">{t(name)}<input className={field} type="number" step={1} min={data.source.start} max={data.source.end} disabled={locked} value={range?.[name] ?? ""} onChange={e => setRange(previous => ({ start: previous?.start ?? data.excerpt.start, end: previous?.end ?? data.excerpt.end, [name]: e.target.value === "" ? NaN : Number(e.target.value) }))} /></label>)}</div>
          <label className="block text-sm">{t("kind")}<select className={field} value={kind} disabled={locked} onChange={e => setKind(e.target.value as MappingInput["kind"])}><option value="manual_adaptation">{t("manual_adaptation")}</option><option value="exact_quote" disabled={target?.kind === "scene"}>{t("exact_quote")}</option></select></label>
          <label className="block text-sm">{t("note")}<textarea className={field} maxLength={1000} value={note} disabled={locked} onChange={e => setNote(e.target.value)} /></label>
          <button className={button} disabled={locked || !range || !target} onClick={apply}>{t("apply")}</button>
        </section> : null}
        <section className="min-w-0 space-y-4">
          <h3 className="font-medium">{t("source")} · {data.source.label}</h3><p className="text-xs leading-6 text-slate-400">{t(data.source.snapshotId ? "snapshot" : "legacy")}</p>
          <label className="block text-sm">{t("chapter")}<select className={field} value={data.chapters.find(ch => ch.start <= data.excerpt.start && ch.end > data.excerpt.start)?.id ?? ""} disabled={busy} onChange={e => { const ch = data.chapters.find(item => item.id === e.target.value); if (ch) setOffset(Math.max(data.source.start, ch.start)); }}>{data.chapters.map(ch => <option key={ch.id} value={ch.id}>{ch.ordinal}. {ch.title ?? t(ch.kind)}</option>)}</select></label>
          <form className="flex items-end gap-2" onSubmit={e => { e.preventDefault(); setQuery(find); setReload(v => v + 1); }}><label className="min-w-0 flex-1 text-sm">{t("find")}<input className={field} maxLength={500} value={find} disabled={busy} onChange={e => setFind(e.target.value)} /></label><button className={button} disabled={busy || !find.trim()}>{t("findAction")}</button></form>
          {query ? <div className="text-xs text-slate-400"><p>{data.matches.length ? t("matches", { count: data.matches.length }) : t("noMatches")}</p>{data.moreMatches ? <p>{t("moreMatches")}</p> : null}<div className="mt-2 flex max-h-32 flex-wrap gap-2 overflow-auto">{data.matches.map(match => <button key={match.start} className={button} disabled={busy} onClick={() => { setOffset(match.start); if (!locked) setRange(match); }}>{match.start}–{match.end}</button>)}</div></div> : null}
          <p className="text-xs leading-6 text-slate-400">{t("selectHelp")}</p>
          <textarea aria-label={t("source")} className={field + " min-h-96 resize-y leading-8"} readOnly value={data.excerpt.text} onSelect={event => {
            if (locked) return;
            const node = event.currentTarget;
            if (node.selectionEnd > node.selectionStart) setRange({ start: data.excerpt.start + Array.from(node.value.slice(0, node.selectionStart)).length, end: data.excerpt.start + Array.from(node.value.slice(0, node.selectionEnd)).length });
          }} />
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500"><button className={button} disabled={busy || data.excerpt.start === data.source.start} onClick={() => setOffset(Math.max(data.source.start, data.excerpt.start - 4000))}>{t("previous")}</button><span>{data.excerpt.start}–{data.excerpt.end} / {data.source.end}</span><button className={button} disabled={busy || data.excerpt.end === data.source.end} onClick={() => setOffset(data.excerpt.end)}>{t("next")}</button></div>
        </section>
      </div>
    </>}
    {draft ? <div className="mt-6 border-t border-white/10 pt-5">
      {pending ? <button className={primary} disabled={busy || !frame.canManage || !data} onClick={() => void submit()}>{t("retry")}</button> : confirm ? <div><p className="text-sm leading-7 text-amber-200">{t("confirmHelp", { count: inputs.length })}</p><div className="mt-3 flex gap-3"><button className={primary} disabled={busy || !frame.canManage || !data?.canManage || conflict} onClick={() => void submit()}>{t("confirm")}</button><button className={button} disabled={busy} onClick={() => setConfirm(false)}>{t("cancel")}</button></div></div> : <button className={primary} disabled={locked || !data} onClick={() => setConfirm(true)}>{t("save")}</button>}
      {!pending && !confirm ? <button className={button + " ml-3 mt-3"} disabled={busy} onClick={() => { if (!discard) { setDiscard(true); return; } if (persist(undefined)) { setDiscard(false); setVersionNumber(script.versions.at(-1)!.number); setRange(undefined); setNote(""); setError(undefined); setReload(value => value + 1); } }}>{t(discard ? "discardConfirm" : "discard")}</button> : null}
    </div> : null}
  </section>;
}
