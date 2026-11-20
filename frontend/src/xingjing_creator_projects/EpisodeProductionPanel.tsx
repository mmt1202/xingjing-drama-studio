import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowRight, BookOpen, Boxes, Check, ChevronDown, Clapperboard, Film, FolderOutput, ListVideo, RefreshCw, Settings2 } from "lucide-react";
import { episodeButton as button, episodeInput as input } from "./EpisodeDetails";
import { EpisodeWorkingScriptAction } from "./EpisodeWorkingScriptPanel";
import { episodeMaterials, episodeNextStep, episodeStages } from "./episode-workflow";
import { useEpisodeWorkflowTranslation } from "./episode-workflow-i18n";
import type { Episode, EpisodeProduction, EpisodesApi, EpisodesSnapshot, ProductionCatalog } from "./episodes-api";

const icons = { script: BookOpen, assets: Boxes, shots: Clapperboard, film: Film, delivery: FolderOutput };
const primary = "inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-violet-200 px-4 py-2 text-sm font-semibold text-[#201335] transition hover:bg-violet-100 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-300 disabled:cursor-not-allowed disabled:opacity-50";
const disclosure = "inline-flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-white/5 hover:text-white aria-expanded:bg-white/5 aria-expanded:text-violet-200 focus-visible:outline-2 focus-visible:outline-violet-300";
const pages = {
  importSource: "script-import", parse: "script-lab", finalize: "versions", extract: "assets-extract", planShots: "shots-smart",
  makeShots: "storyboard", referenceVideo: "generation/video", editFilm: "postproduction", review: "compliance",
  deliver: "release-package", openPackage: "release-package", lockedInputs: "shots-smart",
} as const;

interface Props {
  api: EpisodesApi; projectId: string; data: EpisodesSnapshot; items: Episode[]; current?: Episode;
  catalog?: ProductionCatalog; loadFailed: boolean; disabled: boolean; dirty: boolean; canCreate: boolean;
  onBusy: (value: boolean) => void; onAccepted: (snapshot: EpisodesSnapshot) => void; onRefresh: () => void;
  onNavigate: (target: string) => void; update: (production: EpisodeProduction) => void; plan: ReactNode;
}

export function EpisodeProductionPanel({ api, projectId, data, items, current, catalog, loadFailed, disabled, dirty, canCreate, onBusy, onAccepted, onRefresh, onNavigate, update, plan }: Props) {
  const { t, i18n } = useEpisodeWorkflowTranslation();
  const id = useId();
  const [section, setSection] = useState<"versions" | "settings">();
  const focusField = useRef<"script" | "assets" | "film" | "source" | undefined>(undefined);
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusField.current) body.current?.querySelector<HTMLSelectElement>(`[data-episode-field="${focusField.current}"]`)?.focus();
    focusField.current = undefined;
  }, [section]);
  if (!current) return <div className="flex items-center gap-4 rounded-xl border border-white/10 bg-[#12141b] p-5">
    <ListVideo size={24} className="shrink-0 text-violet-300" aria-hidden="true" /><div><h2 className="font-semibold">{t("choose")}</h2><p className="mt-1 text-sm text-slate-400">{t("chooseHelp")}</p></div>
  </div>;
  const saved = dirty ? data.items.find(item => item.episode === current.episode) ?? current : current;
  const editable = catalog && episodeMaterials(current, items, catalog);
  const materials = saved !== current && catalog ? episodeMaterials(saved, data.items, catalog) : editable;
  const referenceVideo = data.generationMode === "reference_video";
  const next = catalog && materials && episodeNextStep(saved, materials, catalog.permissions, data.sources, referenceVideo);
  const fresh = Boolean(catalog && catalog.revision === data.revision && !loadFailed);
  const has = (permission: string) => Boolean(catalog?.permissions.includes(permission));
  const locked = Boolean(materials?.record?.boards.length);
  const editDisabled = disabled || !fresh || !has("project.manage");
  const draftAction = next?.action === "createDraft" || next?.action === "replaceDraft";
  const denied = Boolean(next?.permission && !has(next.permission)) || (draftAction && !canCreate);
  const showField = (field: NonNullable<typeof focusField.current>) => {
    const target = field === "source" ? "settings" : "versions";
    if (section === target) body.current?.querySelector<HTMLSelectElement>(`[data-episode-field="${field}"]`)?.focus();
    else { focusField.current = field; setSection(target); }
  };
  function open(page: string, values: Record<string, string> = {}) {
    const query = new URLSearchParams({ returnTo: "episodes", ...values });
    if (materials?.final && ["release-package", "compliance"].includes(page)) query.set("projectVersion", materials.final.id);
    onNavigate(`${page}?${query}`);
  }
  function advance() {
    if (!next || !materials || disabled || denied || !fresh) return;
    const { action } = next;
    const { binding, workingFinal, snapshots, finals } = materials;
    if (action === "chooseSource") return showField("source");
    if (action === "useScript" && workingFinal) return update({ ...binding, scriptVersionId: workingFinal.id, assetSnapshotId: "" });
    if (action === "fixScript") return showField("script");
    if (action === "useAssets" && snapshots.length === 1) return update({ ...binding, assetSnapshotId: snapshots[0].id });
    if (action === "fixAssets" && !snapshots.length && !locked) return open("assets-extract");
    if (action === "useAssets" || action === "fixAssets") return showField("assets");
    if (action === "useFilm" && finals.length === 1) return update({ ...binding, finalVideoVersionId: finals[0].id });
    if (action === "fixFilm" && !finals.length) return open("postproduction");
    if (action === "useFilm" || action === "fixFilm") return showField("film");
    if (action in pages) open(pages[action as keyof typeof pages], action === "openPackage" && materials.release ? { target: materials.release.target } : {});
  }
  const binding = editable?.binding;
  const completed = {
    script: Boolean(materials?.script || materials?.workingFinal), assets: Boolean(materials?.assets),
    shots: Boolean(materials?.shots), film: Boolean(materials?.final || materials?.finals.length), delivery: materials?.record?.state === "package_ready",
  };
  return <section aria-labelledby={id + "-title"} className="overflow-hidden rounded-2xl border border-violet-300/20 bg-[#12141d]">
    <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-5 pt-5 sm:px-6">
      <div className="min-w-0"><p className="mb-1 flex items-center gap-2 text-xs font-medium text-violet-300"><Clapperboard size={15} aria-hidden="true" />{t("guide")}</p>
        <h2 id={id + "-title"} className="break-words text-xl font-semibold text-slate-50">{t("episode", { number: current.episode })}<span className="mx-2 text-slate-500">·</span>{current.title}</h2></div>
      <button type="button" className={disclosure} disabled={disabled} onClick={onRefresh}><RefreshCw size={14} aria-hidden="true" />{t("reload")}</button>
    </header>
    {fresh && next && materials ? <>
      <ol aria-label={t("stages")} className="mx-5 my-4 flex flex-wrap gap-x-1 gap-y-2 sm:mx-6">
        {episodeStages.filter(stage => stage !== "shots" || !referenceVideo || completed.shots).map((stage, index) => {
          const active = stage === next.stage, done = completed[stage], Icon = done ? Check : icons[stage];
          return <li key={stage} aria-current={active ? "step" : undefined} className="flex items-center">
            {index > 0 && <span aria-hidden="true" className="mx-1 hidden h-px w-4 bg-white/15 sm:inline-block" />}
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-medium ${active ? "bg-violet-300/15 text-violet-200 ring-1 ring-inset ring-violet-300/30" : done ? "text-emerald-300" : "text-slate-400"}`}>
              <Icon size={14} aria-hidden="true" />{t(stage)}{done && <span className="sr-only"> · {t("done")}</span>}</span>
          </li>;
        })}
      </ol>
      <div className="mx-5 mb-4 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-violet-300/15 bg-violet-300/[.045] p-4 sm:mx-6">
        <div className="min-w-0 flex-1 basis-64"><h3 className="text-sm font-semibold text-violet-100">{t("next")} · {t(next.stage)}</h3>
          <p className="mt-1.5 text-sm leading-6 text-slate-300">{next.action === "useAssets" && materials.snapshots.length === 1
            ? t("knownAssets", { title: materials.script?.title, n: materials.script?.version, count: materials.snapshots[0].assets.length })
            : t(`${next.action}Help`, { title: materials.script?.title, n: materials.script?.version, count: next.action === "planShots" ? materials.assets?.assets.length : materials.shots })}</p>
          {denied && <p className="mt-1 text-xs text-amber-200">{t("noAccess")}</p>}{dirty && <p role="status" className="mt-1 text-xs text-amber-200">{t("saving")}</p>}
        </div>
        {draftAction ? <EpisodeWorkingScriptAction key={data.revision} api={api} projectId={projectId} revision={data.revision} episode={current} sources={data.sources} canCreate={canCreate} disabled={disabled} onBusy={onBusy} onAccepted={onAccepted} primaryClass={primary} /> :
          next.action !== "restricted" && <button type="button" className={primary} disabled={disabled || denied} onClick={advance}>{next.action === "useAssets" && materials.snapshots.length === 1 ? t("adoptAssets", { count: materials.snapshots[0].assets.length }) : t(next.action)}<ArrowRight size={16} aria-hidden="true" /></button>}
      </div>
    </> : <p role={loadFailed ? "alert" : "status"} className="px-6 py-5 text-sm text-slate-300">{t(loadFailed ? "loadError" : catalog ? "stale" : "loading")}</p>}
    <div className="flex flex-wrap gap-1 border-t border-white/10 px-3 py-1.5 sm:px-4">
      {(["versions", "settings"] as const).map(key => { const Icon = key === "versions" ? Boxes : Settings2; return <button key={key} type="button" aria-expanded={section === key} aria-controls={id + "-details"} className={disclosure} onClick={() => setSection(section === key ? undefined : key)}>
        <Icon size={15} aria-hidden="true" />{t(key)}<ChevronDown size={14} aria-hidden="true" className={section === key ? "rotate-180" : ""} /></button>; })}
    </div>
    <div id={id + "-details"} ref={body} hidden={!section} className="border-t border-white/10">
      {section === "settings" && plan}
      {section === "versions" && (editable && binding && fresh ? <div className="space-y-4 p-5 sm:px-6">
        <div className="grid gap-4 md:grid-cols-3">
          <label className="text-sm text-slate-300">{t("scriptLabel")}<select data-episode-field="script" className={input} value={binding.scriptVersionId} disabled={editDisabled || locked || !has("script.view")} onChange={e => update({ ...binding, scriptVersionId: e.target.value, assetSnapshotId: "" })}>
            <option value="">{t("noChoice")}</option>{binding.scriptVersionId && !editable.scripts.some(item => item.id === binding.scriptVersionId) && <option value={binding.scriptVersionId}>{t("unavailable")}</option>}
            {editable.scripts.map(script => <option key={script.id} value={script.id}>{script.title} · {t("version", { n: script.version })}</option>)}
          </select></label>
          <label className="text-sm text-slate-300">{t("assetLabel")}<select data-episode-field="assets" className={input} value={binding.assetSnapshotId} disabled={editDisabled || locked || !binding.scriptVersionId || !has("asset.view")} onChange={e => update({ ...binding, assetSnapshotId: e.target.value })}>
            <option value="">{t("noChoice")}</option>{binding.assetSnapshotId && !editable.assets && <option value={binding.assetSnapshotId}>{t("unavailable")}</option>}
            {editable.snapshots.map((snapshot, index) => <option key={snapshot.id} value={snapshot.id}>{t("assetOption", { n: index + 1, count: snapshot.assets.length })}</option>)}
          </select></label>
          <label className="text-sm text-slate-300">{t("filmLabel")}<select data-episode-field="film" className={input} value={binding.finalVideoVersionId} disabled={editDisabled || !has("final.view")} onChange={e => update({ ...binding, finalVideoVersionId: e.target.value })}>
            <option value="">{t("noChoice")}</option>{binding.finalVideoVersionId && !editable.final && <option value={binding.finalVideoVersionId}>{t("unavailable")}</option>}
            {editable.finals.map((film, index) => <option key={film.id} value={film.id}>{t("filmOption", { n: editable.finals.length - index, date: new Date(film.createdAt).toLocaleString(i18n.language) })}</option>)}
          </select></label>
        </div>
        <p className="text-xs leading-5 text-slate-400">{t(locked ? "inputLock" : "autoSave")}</p>
        <div className="flex flex-wrap gap-2">
          {current.workingScript && has("script.view") && ([["source", "editScript"], ["script-lab", "analyzeScript"], ["versions", "compareScript"]] as const).map(([page, label]) => <button key={page} type="button" className={button} disabled={disabled} onClick={() => open(page)}>{t(label)}</button>)}
          {editable.shots > 0 && has("shot.view") && <button type="button" className={button} disabled={disabled} onClick={() => open("shots")}>{t("lockedInputs")}</button>}
          {has("final.view") && (editable.shots > 0 || editable.finals.length > 0 || binding.finalVideoVersionId) && <button type="button" className={button} disabled={disabled} onClick={() => open("postproduction")}>{t("editFilm")}</button>}
        </div>
        {Boolean(editable.record?.deliveries.length) && <div><h3 className="text-sm font-medium">{t("records")}</h3><ul className="mt-2 space-y-1 text-sm text-slate-400">{editable.record?.deliveries.map(item => <li key={item.id}>{t(item.format === "publish_package" ? "package" : "reviewFile")} · {t(item.target, { defaultValue: item.target })} · {new Date(item.createdAt).toLocaleString(i18n.language)}</li>)}</ul></div>}
      </div> : <p className="p-5 text-sm text-slate-400">{t(loadFailed ? "loadError" : "loading")}</p>)}
    </div>
  </section>;
}
