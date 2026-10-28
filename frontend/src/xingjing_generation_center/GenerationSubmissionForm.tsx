import { useEffect, useRef, useState, type FormEvent } from "react";
import { useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import { GenerationCenterApiError, type GenerationCenterApi } from "./api";
import type { GenerationMediaType, GenerationModelAvailability, GenerationSubmission, GenerationTask, GenerationWorkspaceScope } from "./contracts";
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
  const [modelChoice, setModelChoice] = useState(recovered?.modelChoice ?? "");
  const [availability, setAvailability] = useState<GenerationModelAvailability>();
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
  const dirty = Boolean(prompt || inputs.length || parameters !== "{}" || modelChoice);
  const locked = busy || uncertain;
  useEffect(() => {
    rememberSubmission(recoveryKey, { prompt, inputs, parameters, modelChoice, operation: operation.current, created });
  }, [recoveryKey, prompt, inputs, parameters, modelChoice, uncertain, created]);
  useProjectNavigationBlocker(dirty || locked);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const abort = new AbortController();
    void api.models(scope.projectId, abort.signal).then((value) => {
      if (!abort.signal.aborted) { setAvailability(value); setModelError(false); }
    }).catch(() => { if (!abort.signal.aborted) setModelError(true); })
      .finally(() => { if (!abort.signal.aborted) setChecking(false); });
    return () => abort.abort();
  }, [api, scope.projectId, refresh]);
  useEffect(() => {
    if (!dirty && !locked) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, locked]);
  const models = availability?.models?.filter((model) => model.active && model.media_types.includes(config.media)
    && (model.capabilities.includes(capability) || model.capabilities.includes("generation"))) ?? [];
  const selectedModel = models.find((model) => JSON.stringify([model.provider_id, model.model_id]) === modelChoice);
  const configured = Boolean(availability?.submission_available && availability.callback_available && !checking && !modelError);
  const supported = models.length > 0;
  const hasModel = !modelChoice || Boolean(selectedModel);
  const inputReady = !config.acceptsInput || availability?.input_contract_available === true;
  const roleSupported = capability !== "inpaint";
  const mediaType = ["video_extend", "video_stabilize", "video_upscale"].includes(capability) ? "video"
    : ["image_to_video", "first_last_video", "image_edit", "image_upscale", "reference_fusion", "inpaint"].includes(capability) ? "image" : undefined;

  function reset() { setPrompt(""); setInputs([]); setParameters("{}"); setModelChoice(""); setDiscard(false); operation.current = undefined; }
  function changed() { operation.current = undefined; setError(undefined); setCreated(undefined); setRefreshFailed(false); }
  async function execute(original: SubmissionOperation) {
    if (inFlight.current) return;
    if (!rememberSubmission(recoveryKey, { prompt, inputs, parameters, modelChoice, operation: original })) {
      setError(new Error(t("recoveryUnavailable"))); return;
    }
    inFlight.current = true; setBusy(true); setError(undefined);
    try {
      const task = await api.submit(scope.projectId, original.payload, original.key);
      if (!mounted.current) return;
      rememberSubmission(recoveryKey, { prompt: "", inputs: [], parameters: "{}", modelChoice: "", created: task.task_id });
      setCreated(task.task_id); setUncertain(false); reset();
      try { await onSubmitted(task); } catch { if (mounted.current) setRefreshFailed(true); }
    } catch (reason) {
      if (mounted.current) {
        setError(reason);
        setUncertain(!(reason instanceof GenerationCenterApiError) || reason.status === null || reason.status >= 500 || reason.status === 408);
      }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || !configured || !supported || !hasModel || !roleSupported || !inputReady) return;
    if (!prompt.trim()) { setError(new Error(t("promptRequired"))); return; }
    if (config.acceptsInput && !inputs.length) { setError(new Error(t("required"))); return; }
    if (capability === "first_last_video" && (inputs.length !== 2 || inputs.some((input) => input.mediaType !== "image"))) { setError(new Error(t("firstLast"))); return; }
    let parsed: Record<string, unknown>;
    try { const value: unknown = JSON.parse(parameters); if (!value || Array.isArray(value) || typeof value !== "object") throw new Error(); parsed = value as Record<string, unknown>; }
    catch { setError(new Error(t("invalidParameters"))); return; }
    const payload: GenerationSubmission = {
      media_type: config.media, capability, prompt: prompt.trim(), parameters: parsed,
      input_asset_ids: inputs.map((item) => item.assetId), input_asset_references: inputs.flatMap((item) => item.reference ? [item.reference] : []),
      ...(selectedModel ? { requested_provider_id: selectedModel.provider_id, requested_model_id: selectedModel.model_id } : {}),
    };
    const original = operation.current ?? { key: `xj:${scope.projectId}:submit:${crypto.randomUUID()}`, payload };
    operation.current = original; void execute(original);
  }
  return <section className="rounded-xl border border-white/10 bg-[#12141b] p-5" aria-label={t("title")}>
    <h2 className="text-lg font-semibold">{t("title")}</h2><p className="mt-2 text-sm leading-6 text-slate-400">{t("help")}</p>
    <div className="mt-4 rounded-lg border border-white/10 p-3"><p role="status" className="text-xs text-amber-200">{checking ? t("checking") : modelError ? t("modelError") : !configured ? t("unavailable") : !supported ? t("noModel") : ""}</p><button type="button" className={button + " mt-2"} disabled={checking || locked} onClick={() => { setChecking(true); setRefresh((value) => value + 1); }}>{t("recheck")}</button></div>
    <form onSubmit={submit} className="mt-4 space-y-4">
      <label className="block text-sm">{t("prompt")}<textarea rows={4} className={field} maxLength={12000} value={prompt} disabled={locked} onChange={(event) => { setPrompt(event.target.value); changed(); }} /></label>
      {config.acceptsInput ? <GenerationInputPicker key={`${scope.workspaceId}:${scope.projectId}:${mediaType ?? "all"}`} api={api} scope={scope} mediaType={mediaType} selected={inputs} disabled={locked} onChange={(value) => { setInputs(value); changed(); }} /> : null}
      {config.acceptsInput && !checking && !inputReady ? <p role="status" className="text-xs leading-6 text-amber-200">{t("inputUnavailable")}</p> : null}
      {capability === "first_last_video" ? <p className="text-xs text-slate-400">{t("firstLast")}</p> : null}
      {!roleSupported ? <p role="status" className="text-xs text-amber-200">{t("roleUnsupported")}</p> : null}
      <label className="block text-sm">{t("model")}<select className={field} value={modelChoice} disabled={locked || checking} onChange={(event) => { setModelChoice(event.target.value); changed(); }}><option value="">{t("automatic")}</option>{modelChoice && !selectedModel ? <option value={modelChoice} disabled>{t("noModel")}</option> : null}{models.map((model) => <option key={JSON.stringify([model.provider_id, model.model_id])} value={JSON.stringify([model.provider_id, model.model_id])}>{model.display_name} · {model.version}</option>)}</select></label>
      {modelChoice ? <p className="text-xs text-slate-400">{t("changedModel")}</p> : availability?.estimated_cost_minor != null && availability.billing_currency ? <p className="text-xs text-slate-400">{t("estimate", { currency: availability.billing_currency, amount: (availability.estimated_cost_minor / 100).toFixed(2) })}</p> : null}
      <details><summary className="cursor-pointer text-xs text-slate-400">{t("advanced")}</summary><label className="mt-3 block text-xs">{t("parameters")}<textarea className={field + " font-mono"} value={parameters} disabled={locked} maxLength={32000} onChange={(event) => { setParameters(event.target.value); changed(); }} /></label></details>
      {error || uncertain ? <div role="alert" className="rounded-lg border border-amber-400/20 p-3 text-sm text-amber-100"><p>{uncertain ? t("uncertain") : error instanceof Error ? error.message : t("failed")}</p>{error instanceof GenerationCenterApiError ? <p className="mt-2 break-all font-mono text-xs text-slate-400">{error.code}{error.requestId ? ` · ${error.requestId}` : ""}</p> : null}</div> : null}
      {uncertain ? <div className="space-y-3"><button type="button" className={button} disabled={busy} onClick={() => { if (operation.current) void execute(operation.current); }}>{t("retry")}</button><p className="text-xs leading-6 text-slate-400">{t("acknowledgeHelp")}</p><button type="button" className={button} disabled={busy} onClick={() => { setUncertain(false); setError(undefined); operation.current = undefined; }}>{t("acknowledged")}</button></div> : <div className="flex flex-wrap gap-3"><button type="submit" className={button + " bg-violet-500/30"} disabled={busy || !configured || !supported || !hasModel || !roleSupported || !inputReady || !prompt.trim() || Boolean(config.acceptsInput && !inputs.length)}>{t(busy ? "submitting" : "submit")}</button>{dirty ? <button type="button" className={button} disabled={busy} onClick={() => setDiscard(true)}>{t("discard")}</button> : null}</div>}
      {discard ? <div className="rounded-lg border border-white/10 p-3"><p className="text-sm">{t("discardConfirm")}</p><div className="mt-3 flex gap-3"><button type="button" className={button} disabled={locked} onClick={() => { reset(); setError(undefined); }}>{t("discardYes")}</button><button type="button" className={button} onClick={() => setDiscard(false)}>{t("keep")}</button></div></div> : null}
      {created ? <p role="status" className="break-all text-sm text-emerald-200">{t("submitted", { id: created })}{refreshFailed ? ` ${t("refreshFailed")}` : ""}</p> : null}
    </form>
  </section>;
}
