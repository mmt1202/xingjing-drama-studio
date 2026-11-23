import type { GenerationService } from "./services";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import { GenerationCenterApiError, type GenerationCenterApi } from "./api";
import type { GenerationMediaType, GenerationSubmission, GenerationTask, GenerationWorkspaceScope } from "./contracts";
import { BudgetScopePicker, type BudgetScope } from "@/xingjing_budget/BudgetScopePicker";
import { GenerationInputPicker, type SelectedGenerationInput } from "./GenerationInputPicker";
import { useSubmissionTranslation } from "./submission-i18n";
import { readSubmissionRecovery, rememberSubmission, submissionRecoveryKey, type SubmissionOperation } from "./submission-recovery";

interface Props {
  config: { capability: string; media: GenerationMediaType; acceptsInput?: boolean };
  scope: GenerationWorkspaceScope; api: GenerationCenterApi; onSubmitted: (task: GenerationTask) => Promise<void>;
}
const field = "mt-2 block w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-slate-100 focus:border-violet-300";
const button = "rounded-lg border border-white/10 px-4 py-2 text-sm disabled:opacity-40";

export function GenerationSubmissionForm({ config, scope, api, onSubmitted }: Props) {
  const { t } = useSubmissionTranslation();
  const capability = config.capability === "generation" ? "image_generation" : config.capability;
  const [recoveryKey] = useState(() => submissionRecoveryKey(scope.workspaceId, scope.projectId, capability, config.media));
  const [recovered] = useState(() => readSubmissionRecovery(recoveryKey, capability, config.media));
  const [prompt, setPrompt] = useState(recovered?.prompt ?? "");
  const [inputs, setInputs] = useState<readonly SelectedGenerationInput[]>(recovered?.inputs ?? []);
  const [parameters, setParameters] = useState(recovered?.parameters ?? "{}");
  const [budgetScope, setBudgetScope] = useState<BudgetScope | undefined>(recovered?.budgetScope);
  const [budgetReady, setBudgetReady] = useState(false);
  const [availability, setAvailability] = useState<GenerationService>();
  const [checking, setChecking] = useState(true);
  const [modelError, setModelError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [uncertain, setUncertain] = useState(Boolean(recovered?.operation));
  const [created, setCreated] = useState<string | undefined>(recovered?.created);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [discard, setDiscard] = useState(false);
  const operation = useRef<SubmissionOperation | undefined>(recovered?.operation);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const dirty = Boolean(prompt || inputs.length || parameters !== "{}" || (budgetScope && !created));
  const locked = busy || uncertain;
  useEffect(() => {
    rememberSubmission(recoveryKey, { prompt, inputs, parameters, budgetScope, operation: operation.current, created });
  }, [recoveryKey, prompt, inputs, parameters, budgetScope, uncertain, created]);
  useProjectNavigationBlocker(dirty || locked);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const abort = new AbortController();
    void api.services(scope.projectId, config.media, [capability], abort.signal).then((value) => {
      if (!abort.signal.aborted) { setAvailability(value.find((item) => item.capability === capability)); setModelError(false); }
    }).catch(() => { if (!abort.signal.aborted) setModelError(true); })
      .finally(() => { if (!abort.signal.aborted) setChecking(false); });
    return () => abort.abort();
  }, [api, scope.projectId, config.media, capability, refresh]);
  useEffect(() => {
    if (!dirty && !locked) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, locked]);
  const ready = Boolean(availability?.available && !checking && !modelError);
  const inputReady = !config.acceptsInput || availability?.acceptsReferences === true;
  const insufficient = error instanceof GenerationCenterApiError && error.code === "GENERATION_INSUFFICIENT_CREDITS";
  const roleSupported = capability !== "inpaint";
  const mediaType = ["video_extend", "video_stabilize", "video_upscale"].includes(capability) ? "video"
    : ["image_to_video", "first_last_video", "image_edit", "image_upscale", "reference_fusion", "inpaint"].includes(capability) ? "image" : undefined;

  function reset(keepBudget = false) { setPrompt(""); setInputs([]); setParameters("{}"); if (!keepBudget) setBudgetScope(undefined); setDiscard(false); operation.current = undefined; }
  function changed() { operation.current = undefined; setError(undefined); setCreated(undefined); setRefreshFailed(false); }
  async function execute(original: SubmissionOperation) {
    if (inFlight.current) return;
    if (!rememberSubmission(recoveryKey, { prompt, inputs, parameters, budgetScope, operation: original })) {
      setError(new Error(t("recoveryUnavailable"))); return;
    }
    inFlight.current = true; setBusy(true); setError(undefined);
    try {
      const task = await api.submit(scope.projectId, original.payload, original.key);
      if (!mounted.current) return;
      rememberSubmission(recoveryKey, { prompt: "", inputs: [], parameters: "{}", budgetScope, created: task.task_id });
      setCreated(task.task_id); setUncertain(false); reset(true);
      try { await onSubmitted(task); } catch { if (mounted.current) setRefreshFailed(true); }
    } catch (reason) {
      if (mounted.current) {
        setError(reason);
        const unconfirmed = !(reason instanceof GenerationCenterApiError) || (!reason.rejected && (reason.status === null || reason.status >= 500 || reason.status === 408));
        setUncertain(unconfirmed);
        if (!unconfirmed) {
          operation.current = undefined;
          rememberSubmission(recoveryKey, { prompt, inputs, parameters, budgetScope });
          if (reason.rejected) { setChecking(true); setRefresh((value) => value + 1); }
        }
      }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || !ready || !roleSupported || !inputReady || !budgetReady) return;
    if (!prompt.trim()) { setError(new Error(t("promptRequired"))); return; }
    if (config.acceptsInput && !inputs.length) { setError(new Error(t("required"))); return; }
    if (capability === "first_last_video" && (inputs.length !== 2 || inputs.some((input) => input.mediaType !== "image"))) { setError(new Error(t("firstLast"))); return; }
    let parsed: Record<string, unknown>;
    try { const value: unknown = JSON.parse(parameters); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); parsed = value as Record<string, unknown>; }
    catch { setError(new Error(t("invalidParameters"))); return; }
    const payload: GenerationSubmission = {
      media_type: config.media, capability, prompt: prompt.trim(), parameters: parsed,
      ...(budgetScope ? { budget_scope: budgetScope.kind === "episode" ? { kind: "episode" as const, episode_id: budgetScope.episodeId } : { kind: "project_shared" as const } } : {}),
      input_asset_ids: inputs.map((item) => item.assetId), input_asset_references: inputs.flatMap((item) => item.reference ? [item.reference] : []),
    };
    const original = operation.current ?? { key: `xj:${scope.projectId}:submit:${crypto.randomUUID()}`, payload };
    operation.current = original; void execute(original);
  }
  return <section className="rounded-xl border border-white/10 bg-[#12141b] p-5" aria-label={t("title")}>
    <h2 className="text-lg font-semibold">{t("title")}</h2><p className="mt-2 text-sm leading-6 text-slate-400">{t("help")}</p>
    {!ready ? <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-white/10 p-3">
      <p role="status" className="text-sm text-slate-300">{t(checking ? "checking" : modelError ? "modelError" : "unavailable")}</p>
      {!checking && <button type="button" className={button} disabled={locked} onClick={() => { setChecking(true); setRefresh((value) => value + 1); }}>{t("recheck")}</button>}
    </div> : null}
    <form onSubmit={submit} className="mt-4 space-y-4">
      <label className="block text-sm">{t("prompt")}<textarea rows={4} className={field} maxLength={12000} value={prompt} disabled={locked} onChange={(event) => { setPrompt(event.target.value); changed(); }} /></label>
      {config.acceptsInput ? <GenerationInputPicker key={`${scope.workspaceId}:${scope.projectId}:${mediaType ?? "all"}`} api={api} scope={scope} mediaType={mediaType} selected={inputs} disabled={locked} onChange={(value) => { setInputs(value); changed(); }} /> : null}
      {config.acceptsInput && ready && !inputReady ? <p role="status" className="text-xs leading-6 text-amber-200">{t("inputUnavailable")}</p> : null}
      {capability === "first_last_video" ? <p className="text-xs text-slate-400">{t("firstLast")}</p> : null}
      {!roleSupported ? <p role="status" className="text-xs text-amber-200">{t("roleUnsupported")}</p> : null}
      {ready && availability?.estimatedMinor != null && availability.currency ? <p className="text-sm text-slate-300">{t("estimate", { currency: availability.currency, amount: (availability.estimatedMinor / 100).toFixed(2) })}</p> : null}
      <BudgetScopePicker projectId={scope.projectId} workspaceId={scope.workspaceId} value={budgetScope} disabled={locked} onReady={setBudgetReady} onChange={(value) => { setBudgetScope(value); changed(); }} />
      {ready && availability?.successfulOutputMinor != null && availability.currency ? <p className="text-xs text-slate-400">{t("fixedCharge", { currency: availability.currency, amount: (availability.successfulOutputMinor / 100).toFixed(2) })}</p> : null}
      {error || uncertain ? <div role="alert" className="rounded-lg border border-amber-400/20 p-3 text-sm text-amber-100">
        <p>{uncertain ? t("uncertain") : insufficient ? t(availability?.canManageBilling ? "insufficient" : "askOwner") : error instanceof GenerationCenterApiError ? (/^(BUDGET_|PROJECT_BUDGET_|EPISODE_BUDGET_|GENERATION_INPUT_)/.test(error.code) && error.message !== error.code ? error.message : t("taskFailed")) : error instanceof Error ? error.message : t("failed")}</p>
        {insufficient && availability?.canViewBilling ? <a href={`/app/workspaces/${encodeURIComponent(scope.workspaceId)}/billing`} className="mt-2 inline-block underline">{t(availability?.canManageBilling ? "topUpLink" : "balanceLink")}</a> : null}
      </div> : null}
      {uncertain ? <div className="space-y-3"><button type="button" className={button} disabled={busy} onClick={() => { if (operation.current) void execute(operation.current); }}>{t("retry")}</button><p className="text-xs leading-6 text-slate-400">{t("acknowledgeHelp")}</p><button type="button" className={button} disabled={busy} onClick={() => { setUncertain(false); setError(undefined); operation.current = undefined; }}>{t("acknowledged")}</button></div> : <div className="flex flex-wrap gap-3"><button type="submit" className={button + " bg-violet-500/30"} disabled={busy || !ready || !roleSupported || !inputReady || !budgetReady || !prompt.trim() || Boolean(config.acceptsInput && !inputs.length)}>{t(busy ? "submitting" : "submit")}</button>{dirty ? <button type="button" className={button} disabled={busy} onClick={() => setDiscard(true)}>{t("discard")}</button> : null}</div>}
      {discard ? <div className="rounded-lg border border-white/10 p-3"><p className="text-sm">{t("discardConfirm")}</p><div className="mt-3 flex gap-3"><button type="button" className={button} disabled={locked} onClick={() => { reset(); setError(undefined); }}>{t("discardYes")}</button><button type="button" className={button} onClick={() => setDiscard(false)}>{t("keep")}</button></div></div> : null}
      {created ? <p role="status" className="break-all text-sm text-emerald-200">{t("submitted", { id: created })}{refreshFailed ? ` ${t("refreshFailed")}` : ""}</p> : null}
    </form>
  </section>;
}
