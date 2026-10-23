import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Check, CheckCheck, ChevronRight, ImagePlus, Images, Layers, Pencil, Plus, RefreshCw, Sparkles, Star } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useAuthStore } from "@/stores/auth-store";
import { useProjectFlow, useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import { CoverApiError, createCoverApi } from "./api";
import type { CoverCatalog, CoverDetail, CoverPage, CoverPlan, CoverVariant, PlanInput } from "./contracts";
import { EvaluationForm, PlanForm, VariantForm } from "./CoverForms";
import { CoverPreview } from "./CoverPreview";
import { coverButton, coverField, coverPanel, useCoverTranslation } from "./i18n";
import "./cover.css";

function CoverError({ error, retry, retryLabel }: { error: unknown; retry?: () => void; retryLabel?: string }) {
  const { t } = useCoverTranslation();
  const problem = error instanceof CoverApiError ? error : undefined;
  const key = problem?.status === 403 ? "forbidden" : problem?.status === 409 ? "conflict" : problem?.status === 404 ? "missing" : problem?.status === 503 ? "unavailable" : "failed";
  return <div role="alert" className="rounded-xl border border-amber-300/20 bg-amber-300/[.04] p-4 text-sm leading-6 text-amber-100"><p>{t(key)}</p>{problem && <p className="mt-2 break-all text-xs text-slate-400">{problem.code}{problem.requestId ? ` · ${t("requestId")}: ${problem.requestId}` : ""}</p>}{retry ? <button className={coverButton + " mt-3"} onClick={retry}>{retryLabel ?? t("retry")}</button> : null}</div>;
}

export function CoverWorkspace({ workspaceId, projectId, mode }: { workspaceId: string; projectId: string; mode: "generate" | "ab" }) {
  const { t, i18n } = useCoverTranslation();
  const { go } = useProjectFlow();
  const token = useAuthStore((state) => state.token);
  const api = useMemo(() => createCoverApi(workspaceId, projectId, token), [workspaceId, projectId, token]);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const filterKey = new URLSearchParams({ q: params.get("q") ?? "", target: params.get("target") ?? "", finalVersionId: params.get("projectVersion") ?? "", episodeId: params.get("episodeId") ?? "", offset: params.get("offset") ?? "0", limit: "20" }).toString();
  const [reload, setReload] = useState(0);
  const [catalogState, setCatalog] = useState<{ api: typeof api; value?: CoverCatalog; error?: unknown; revision: number }>();
  const [pageState, setPage] = useState<{ key: string; value?: CoverPage; error?: unknown; revision: number }>();
  const [detailState, setDetail] = useState<{ id: string; value?: CoverDetail; error?: unknown; revision: number }>();
  const [editor, setEditor] = useState<"create" | "edit" | "variant" | "evaluate">();
  const [editingVariant, setEditingVariant] = useState<CoverVariant>();
  const [initialAssetId, setInitialAssetId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [writeError, setWriteError] = useState<unknown>();
  const [saved, setSaved] = useState(false);
  const [compared, setCompared] = useState<string[]>([]);
  const locked = useRef(false);
  useProjectNavigationBlocker(busy);
  useEffect(() => {
    const controller = new AbortController();
    void api.catalog(controller.signal).then((value) => { if (!controller.signal.aborted) setCatalog({ api, value, revision: reload }); }).catch((error: unknown) => { if (!controller.signal.aborted) setCatalog((previous) => ({ api, value: previous?.api === api ? previous.value : undefined, error, revision: reload })); });
    return () => controller.abort();
  }, [api, reload]);
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams(filterKey);
    void api.list({ q: query.get("q") ?? "", target: query.get("target") ?? "", finalVersionId: query.get("finalVersionId") ?? "", episodeId: query.get("episodeId") ?? "", offset: Number(query.get("offset") ?? 0), limit: 20 }, controller.signal).then((value) => { if (!controller.signal.aborted) setPage({ key: filterKey, value, revision: reload }); }).catch((error: unknown) => { if (!controller.signal.aborted) setPage((previous) => ({ key: filterKey, value: previous?.key === filterKey ? previous.value : undefined, error, revision: reload })); });
    return () => controller.abort();
  }, [api, filterKey, reload]);
  const catalog = catalogState?.api === api ? catalogState.value : undefined;
  const page = pageState?.key === filterKey ? pageState.value : undefined;
  const selectedId = params.get("coverPlanId") ?? page?.items[0]?.id;
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    void api.detail(selectedId, controller.signal).then((value) => { if (!controller.signal.aborted) setDetail({ id: selectedId, value, revision: reload }); }).catch((error: unknown) => { if (!controller.signal.aborted) setDetail((previous) => ({ id: selectedId, value: previous?.id === selectedId ? previous.value : undefined, error, revision: reload })); });
    return () => controller.abort();
  }, [api, selectedId, reload]);
  const detail = selectedId && detailState?.id === selectedId ? detailState.value : undefined;
  const plan = detail?.plan;
  const pollTasks = !busy && !editor && Boolean(detail?.generationTasks.some((task) => ["queued", "running", "retrying", "cancelling"].includes(task.status)));
  useEffect(() => {
    if (!pollTasks || !selectedId) return;
    const controller = new AbortController();
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void api.detail(selectedId, controller.signal).then((value) => { if (!controller.signal.aborted) setDetail({ id: selectedId, value, revision: reload }); }).catch(() => undefined).finally(() => { pending = false; });
    }, 5000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [api, selectedId, reload, pollTasks]);
  const can = detail?.permissions ?? catalog?.permissions;
  const conflict = writeError instanceof CoverApiError && writeError.status === 409;
  const refreshingDetail = Boolean(selectedId && detailState?.revision !== reload);
  const reading = catalogState?.revision !== reload || pageState?.key !== filterKey || pageState?.revision !== reload;
  const waiting = busy || Boolean(editor) || refreshingDetail || reading;
  const date = (value: string) => new Date(value).toLocaleString(i18n.resolvedLanguage);
  const refresh = () => { setReload((value) => value + 1); };
  function query(values: Record<string, string>) {
    if (waiting) return;
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    navigate(`${location}?${next}`);
  }
  function open(target: "cover-generate" | "cover-title-ab-test" | "release-materials", selected?: CoverPlan) {
    const current = selected ?? plan;
    const next = new URLSearchParams();
    for (const key of ["projectVersion", "episodeId", "target", "coverPlanId", "coverSelectionId"]) { const value = params.get(key); if (value) next.set(key, value); }
    if (current) { next.set("projectVersion", current.finalVersionId); next.set("episodeId", current.episodeId); next.set("target", current.target); next.set("coverPlanId", current.id); if (current.appliedSelectionId) next.set("coverSelectionId", current.appliedSelectionId); else next.delete("coverSelectionId"); }
    go(`${target}${next.size ? `?${next}` : ""}`);
  }
  async function action(name: string, payload: object = {}) {
    if (!plan || locked.current || conflict || refreshingDetail) return false;
    locked.current = true; setBusy(true); setWriteError(undefined); setSaved(false);
    try { await api.action(plan, name, payload); refresh(); setSaved(true); return true; }
    catch (error) { setWriteError(error); return false; }
    finally { locked.current = false; setBusy(false); }
  }
  async function savePlan(input: PlanInput) {
    if (editor !== "create") { const { name, prompt, aspectRatio, titleSafeArea } = input; const success = await action("update", { name, prompt, aspectRatio, titleSafeArea }); if (success) setEditor(undefined); return success; }
    if (locked.current) return false;
    locked.current = true; setBusy(true); setWriteError(undefined);
    try {
      const result = await api.create(input);
      const next = new URLSearchParams(search); next.set("coverPlanId", result.plan.id); next.delete("offset"); next.delete("q"); next.set("projectVersion", result.plan.finalVersionId); next.set("target", result.plan.target); next.set("episodeId", result.plan.episodeId);
      setEditor(undefined); setSaved(true); refresh(); navigate(`${location}?${next}`); return true;
    } catch (error) { setWriteError(error); return false; }
    finally { locked.current = false; setBusy(false); }
  }
  function editVariant(variant?: CoverVariant, assetId?: string) { setInitialAssetId(assetId); setEditingVariant(variant); setEditor("variant"); setWriteError(undefined); }
  function searchPlans(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const value = new FormData(event.currentTarget).get("q"); query({ q: typeof value === "string" ? value.trim() : "", offset: "0", coverPlanId: "" }); }
  if (catalogState?.api === api && catalogState.error && !catalog) return <main className="xj-cover-page"><h1 className="mb-5 text-2xl font-semibold">{t(mode === "ab" ? "titleAb" : "titleGenerate")}</h1><CoverError error={catalogState.error} retry={refresh} /></main>;
  if (!catalog) return <main className="xj-cover-page" aria-busy="true"><p role="status" className="py-12 text-slate-400">{t("loading")}</p></main>;
  const availableCandidates = detail?.candidates.filter((candidate) => candidate.available) ?? [];
  const selectedVariant = detail?.variants.find((variant) => variant.id === plan?.selectedVariantId);
  const running = detail?.generationTasks.filter((task) => ["queued", "running", "retrying", "cancelling"].includes(task.status)).length ?? 0;
  const quotation = catalog.generationAvailability;
  const estimate = quotation?.billing_currency && quotation.estimated_cost_minor !== null && Number.isSafeInteger(quotation.estimated_cost_minor) ? new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: quotation.billing_currency }).format(quotation.estimated_cost_minor / 100) : "—";
  return <main className="xj-cover-page">
    <header className="xj-cover-hero"><div className="xj-cover-toolbar"><div><span className="text-xs text-violet-300">{t("projectMaterials")}</span><h1 className="mt-3 text-3xl font-semibold">{t(mode === "ab" ? "titleAb" : "titleGenerate")}</h1><p className="mt-3 text-sm leading-6 text-slate-400">{t(mode === "ab" ? "subtitleAb" : "subtitleGenerate")}</p></div><button className={coverButton} disabled={waiting} onClick={refresh}><RefreshCw size={15} />{t("refresh")}</button></div>
      <nav className="mt-5 flex flex-wrap gap-2" aria-label={t("navigation")}><button className={coverButton + (mode === "generate" ? " border-violet-300/40 bg-violet-400/10 text-violet-100" : "")} disabled={busy} aria-current={mode === "generate" ? "page" : undefined} onClick={() => open("cover-generate")}>{t("titleGenerate")}</button><button className={coverButton + (mode === "ab" ? " border-violet-300/40 bg-violet-400/10 text-violet-100" : "")} disabled={busy} aria-current={mode === "ab" ? "page" : undefined} onClick={() => open("cover-title-ab-test")}>{t("titleAb")}</button></nav>
      <div className="xj-cover-metrics">{[["candidates", detail?.candidates.length ?? 0, Images], ["usable", availableCandidates.length, CheckCheck], ["processing", running, Layers], ["estimate", estimate, Sparkles]].map(([label, value, Icon]) => { const MetricIcon = Icon as typeof Images; return <div key={String(label)}><span className="flex items-center justify-between gap-2 text-xs text-slate-400">{t(String(label))}<MetricIcon size={16} aria-hidden="true" /></span><strong className="mt-3 block text-2xl tabular-nums">{String(value)}</strong></div>; })}</div>
    </header>
    {catalogState?.api === api && catalogState.error ? <CoverError error={catalogState.error} retry={editor ? undefined : refresh} /> : null}
    {writeError ? <div className="my-4"><CoverError error={writeError} retry={conflict && editor ? undefined : () => { if (!editor) refresh(); setWriteError(undefined); }} retryLabel={editor ? t("continueEditing") : undefined} />{conflict && editor ? <button className={coverButton + " mt-3"} disabled={busy} onClick={() => { setEditor(undefined); setWriteError(undefined); refresh(); }}>{t("discardReload")}</button> : null}</div> : saved ? <p role="status" className="my-4 text-sm text-emerald-300">{t("saved")}</p> : null}
    <div className="xj-cover-layout"><div className="xj-cover-main">
      <section className={coverPanel}><div className="xj-cover-toolbar"><h2 className="text-lg font-semibold">{t("planList")}</h2><button className={coverButton} disabled={waiting || !catalog.permissions.canManage || !catalog.finalVideos.length} onClick={() => { setEditor("create"); setWriteError(undefined); }}><Plus size={15} />{t("create")}</button></div>
        <form className="my-4 flex flex-wrap gap-2" onSubmit={searchPlans}><input className={coverField + " min-w-0 flex-1"} key={params.get("q")} name="q" maxLength={255} defaultValue={params.get("q") ?? ""} aria-label={t("search")} placeholder={t("search")} disabled={waiting} /><button className={coverButton} disabled={waiting}>{t("searchAction")}</button><select aria-label={t("target")} className={coverField} value={params.get("target") ?? ""} disabled={waiting} onChange={(event) => query({ target: event.target.value, offset: "0", coverPlanId: "" })}><option value="">{t("all")}</option>{catalog.targets.map((target) => <option key={target} value={target}>{t(`platform.${target}`, { defaultValue: target })}</option>)}</select></form>
        {!catalog.finalVideos.length ? <p className="py-4 text-sm leading-6 text-slate-400">{t("emptyFinals")}</p> : null}
        {pageState?.key === filterKey && pageState.error ? <CoverError error={pageState.error} retry={editor ? undefined : refresh} /> : !page ? <p role="status" className="py-6 text-slate-400">{t("loading")}</p> : <><ul className="xj-cover-list">{page.items.map((item) => <li key={item.id}><button className={`w-full rounded-xl border px-4 py-3 text-left ${selectedId === item.id ? "border-violet-300/35 bg-violet-400/[.08]" : "border-white/10 hover:bg-white/[.03]"}`} disabled={waiting} aria-current={selectedId === item.id ? "true" : undefined} onClick={() => { query({ coverPlanId: item.id }); setCompared([]); setSaved(false); }}><span className="flex flex-wrap justify-between gap-2 text-sm"><strong>{item.name}</strong><span className="text-xs text-slate-400">{t(`platform.${item.target}`, { defaultValue: item.target })} · {item.aspectRatio}</span></span><span className="mt-2 block break-all text-[11px] text-slate-500">{item.finalVersionId} · {t("version")} {item.version} · {date(item.updatedAt)}</span></button></li>)}</ul>{!page.items.length ? <p className="py-6 text-sm text-slate-400">{t("empty")} {t("emptyHint")}</p> : null}<div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="flex gap-2"><button className={coverButton} disabled={waiting || page.offset === 0} onClick={() => query({ offset: String(Math.max(0, page.offset - page.limit)), coverPlanId: "" })}>{t("previous")}</button><button className={coverButton} disabled={waiting || page.offset + page.limit >= page.total} onClick={() => query({ offset: String(page.offset + page.limit), coverPlanId: "" })}>{t("next")}</button></div></div></>}
      </section>
      {editor === "create" || editor === "edit" ? <PlanForm key={editor === "edit" ? plan?.id : "new"} catalog={catalog} initialFinalVersionId={params.get("projectVersion") ?? undefined} initialTarget={params.get("target") ?? undefined} plan={editor === "edit" ? plan : undefined} busy={busy || conflict || refreshingDetail} onSave={savePlan} onCancel={() => setEditor(undefined)} /> : null}
      {detailState && detailState.id === selectedId && detailState.error ? <CoverError error={detailState.error} retry={editor ? undefined : refresh} /> : selectedId && !detail ? <p role="status" className="p-6 text-sm text-slate-400">{t("loading")}</p> : null}
      {detail && plan ? <>
        {mode === "generate" ? <section className={coverPanel}><div className="xj-cover-toolbar"><div><h2 className="text-lg font-semibold">{t("candidates")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("candidateHint")}</p></div><button className={coverButton + " xj-cover-button--primary"} disabled={waiting || conflict || !can?.canGenerate || !quotation?.submission_available || !quotation.callback_available || !plan.prompt.trim()} onClick={() => void action("generate", { capability: "image_generation", parameters: { aspect_ratio: plan.aspectRatio } })}><Sparkles size={15} />{t("generate")}</button></div>
          {!quotation?.submission_available || !quotation.callback_available ? <p className="mt-4 text-xs leading-6 text-amber-200">{t("modelUnavailable")}</p> : null}
          <form className="mt-4 flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); const assetId = new FormData(event.currentTarget).get("assetId"); if (typeof assetId === "string" && assetId.trim()) void action("attachCandidate", { assetId: assetId.trim() }); }}><input name="assetId" required maxLength={128} placeholder={t("assetId")} aria-label={t("assetId")} disabled={waiting || conflict || !can?.canManage} className={coverField + " min-w-0 flex-1"} /><button className={coverButton} disabled={waiting || conflict || !can?.canManage}><ImagePlus size={15} />{t("attach")}</button></form>
          <div className="mt-5 grid gap-4 sm:grid-cols-2">{detail.candidates.map((candidate) => <article key={candidate.assetId} className="overflow-hidden rounded-xl border border-white/10"><CoverPreview api={api} plan={plan} candidate={candidate} overlay /><div className="p-3"><p className="truncate font-mono text-[10px] text-slate-500" title={candidate.assetId}>{candidate.assetId}</p><div className="mt-3 flex justify-between gap-2"><span className="text-xs text-slate-400">{t(candidate.available ? "usable" : "mediaUnavailable")}</span><button className="text-xs text-violet-200 disabled:opacity-40" disabled={waiting || !can?.canManage || !candidate.available} onClick={() => editVariant(undefined, candidate.assetId)}>{t("addVariant")}<ChevronRight size={13} className="ml-1 inline" /></button></div></div></article>)}</div>{!detail.candidates.length ? <p className="py-8 text-sm text-slate-400">{t("emptyCandidates")}</p> : null}
          <ul className="mt-5 divide-y divide-white/10">{detail.generationTasks.map((task) => <li key={task.task_id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-xs"><span className="min-w-0 break-all text-slate-400">{task.task_id} · {task.resolved_model_id ?? task.request?.requested_model_id ?? t("workspaceRoute")} · {t(`taskState.${task.status}`, { defaultValue: task.status })}</span><button className="text-violet-200" onClick={() => go(`tasks?taskId=${encodeURIComponent(task.task_id)}`)}>{t("openGeneration")}<ArrowRight size={12} className="ml-1 inline" /></button></li>)}</ul>
        </section> : null}
        <section className={coverPanel}><div className="xj-cover-toolbar"><div><h2 className="text-lg font-semibold">{t("variants")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("manualEvaluation")}</p></div><button className={coverButton} disabled={waiting || conflict || !can?.canManage || !availableCandidates.length} onClick={() => editVariant()}><Plus size={15} />{t("addVariant")}</button></div>
          {editor === "variant" ? <div className="mt-5"><VariantForm candidates={availableCandidates} variant={editingVariant} initialAssetId={initialAssetId} busy={busy || conflict || refreshingDetail} onSave={async (input) => { const success = await action("saveVariant", input); if (success) setEditor(undefined); return success; }} onCancel={() => setEditor(undefined)} /></div> : editor === "evaluate" && editingVariant ? <div className="mt-5"><EvaluationForm variant={editingVariant} busy={busy || conflict || refreshingDetail} onSave={async (score, note) => { const success = await action("evaluateVariant", { variantId: editingVariant.id, score, note }); if (success) setEditor(undefined); return success; }} onCancel={() => setEditor(undefined)} /></div> : null}
          <div className="mt-5 grid gap-4 sm:grid-cols-2">{detail.variants.filter((variant) => compared.length !== 2 || compared.includes(variant.id)).map((variant) => <article key={variant.id} className={`min-w-0 overflow-hidden rounded-xl border ${plan.selectedVariantId === variant.id ? "border-violet-300/40" : "border-white/10"}`}><CoverPreview api={api} plan={plan} candidate={detail.candidates.find((candidate) => candidate.assetId === variant.coverAssetId)} title={variant.title} overlay /><div className="space-y-3 p-4"><div className="flex flex-wrap justify-between gap-2"><strong className="text-sm">{variant.label}</strong><label className="flex items-center gap-2 text-xs text-slate-400"><input type="checkbox" checked={compared.includes(variant.id)} disabled={!compared.includes(variant.id) && compared.length === 2} onChange={(event) => setCompared(event.target.checked ? [...compared, variant.id] : compared.filter((id) => id !== variant.id))} />{t("compare")}</label></div><h3 className="break-words text-base font-semibold">{variant.title}</h3><p className="break-words text-xs leading-6 text-slate-400">{variant.description}</p><p className="break-words text-xs text-violet-200/80">{variant.tags.map((tag) => `#${tag}`).join(" ")}</p><p className="text-xs text-slate-400">{t("score")}: {variant.score ?? "—"} / 100</p>{variant.evaluationNote ? <p className="break-words text-xs leading-6 text-slate-500">{variant.evaluationNote}</p> : null}<div className="flex flex-wrap gap-2 text-[11px] text-violet-200">{plan.selectedVariantId === variant.id ? <span><Check size={12} className="mr-1 inline" />{t("selected")}</span> : null}{plan.recommendedVariantId === variant.id ? <span><Star size={12} className="mr-1 inline" />{t("recommended")}</span> : null}</div><div className="flex flex-wrap gap-2"><button className={coverButton} disabled={waiting || conflict || !can?.canManage} onClick={() => editVariant(variant)}><Pencil size={13} />{t("edit")}</button><button className={coverButton} disabled={waiting || conflict || !can?.canManage} onClick={() => { setEditingVariant(variant); setEditor("evaluate"); }}>{t("evaluate")}</button><button className={coverButton} disabled={waiting || conflict || !can?.canManage || variant.score === null || !variant.evaluationNote} onClick={() => void action("recommendVariant", { variantId: variant.id })}>{t("recommend")}</button><button className={coverButton} disabled={waiting || conflict || !can?.canManage || variant.score === null || !variant.evaluationNote} onClick={() => void action("selectVariant", { variantId: variant.id })}>{t("select")}</button></div></div></article>)}</div>{!detail.variants.length ? <p className="py-8 text-sm text-slate-400">{t("emptyVariants")}</p> : null}{compared.length ? <button className={coverButton + " mt-4"} onClick={() => setCompared([])}>{t("showAll")}</button> : null}
        </section>
        <section className={coverPanel}><h2 className="mb-4 text-lg font-semibold">{t("history")}</h2><ol className="space-y-3">{detail.history?.map((item) => <li key={item.id} className="flex flex-wrap items-baseline justify-between gap-2 border-l-2 border-violet-400/30 pl-3 text-xs"><span className="text-slate-300">{t("version")} {item.version} · {t(`action.${item.action}`, { defaultValue: item.action })}</span><span className="text-slate-500">{date(item.createdAt)} · {item.actorId}</span></li>)}</ol>{!detail.history?.length ? <p className="text-xs text-slate-500">{t("version")} {plan.version} · {date(plan.updatedAt)}</p> : null}</section>
      </> : null}
    </div><aside className="xj-cover-inspector"><section className={coverPanel}><div className="xj-cover-toolbar"><h2 className="text-lg font-semibold">{t("currentPlan")}</h2>{plan ? <button className={coverButton} disabled={waiting || !can?.canManage} onClick={() => { setEditor("edit"); setWriteError(undefined); }}><Pencil size={14} /><span className="sr-only">{t("edit")}</span></button> : null}</div>{plan ? <><h3 className="mt-5 break-words text-lg font-semibold">{plan.name}</h3><dl className="mt-4 space-y-4 text-xs">{[["finalVersion", plan.finalVersionId], ["target", t(`platform.${plan.target}`, { defaultValue: plan.target })], ["aspectRatio", plan.aspectRatio], ["version", String(plan.version)], ["updated", date(plan.updatedAt)]].map(([label, value]) => <div key={label} className="flex flex-wrap justify-between gap-2 border-b border-white/[.06] pb-3"><dt className="text-slate-500">{t(label)}</dt><dd className="max-w-full break-all text-slate-300">{value}</dd></div>)}</dl><p className="mt-4 break-words text-xs leading-6 text-slate-400">{plan.prompt}</p><div className="mt-5 space-y-3"><button className={coverButton + " xj-cover-button--primary w-full"} disabled={waiting || conflict || !can?.canApply || !selectedVariant} onClick={() => void action("apply")}><CheckCheck size={15} />{t("apply")}</button>{plan.appliedSelectionId ? <p className="break-all text-xs leading-6 text-emerald-300">{t("applied")} · {plan.appliedSelectionId}</p> : null}<button className={coverButton + " w-full"} disabled={waiting || !plan.appliedSelectionId} onClick={() => open("release-materials")}>{t("releaseMaterials")}<ArrowRight size={14} /></button></div></> : <p className="mt-5 text-sm text-slate-400">{t("emptyHint")}</p>}</section>{plan && detail ? <section className={coverPanel}><h2 className="mb-4 text-lg font-semibold">{t("preview")}</h2><CoverPreview api={api} plan={plan} candidate={detail.candidates.find((candidate) => candidate.assetId === selectedVariant?.coverAssetId) ?? availableCandidates[0]} title={selectedVariant?.title} overlay /><p className="mt-3 text-xs leading-6 text-slate-500">{t("layoutHint")}</p></section> : null}</aside></div>
  </main>;
}
