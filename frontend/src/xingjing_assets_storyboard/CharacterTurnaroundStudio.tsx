import { useEffect, useState } from "react";
import { ArrowRight, Box, Check, ClipboardPenLine, Images, Layers3, Save, ScanFace, Sparkles } from "lucide-react";
import { AssetImageBilling, ReferencePreview, assetHasReferenceImage } from "./AssetReferenceGeneration";
import { DraftRecoveryNotice } from "./DraftRecoveryNotice";
import { useAssetGenerationTranslation } from "./asset-generation-i18n";
import type { AssetImageControls } from "./asset-image-models";
import type { AssetItem, AssetVersion, TaskReceipt } from "./contracts";
import type { ProductionDraftScope } from "./draft-state";
import { useTurnaroundTranslation } from "./turnaround-i18n";
import { useRecoverableDraft } from "./useRecoverableDraft";
import "./turnaround.css";

type Stage = "reference" | "sheet";
const views = {
  front: "full-body front view", side: "full-body side profile", backView: "full-body back view",
  threeQuarter: "three-quarter view", closeUp: "face close-up", costume: "costume detail",
} as const;
type View = keyof typeof views;
const basicViews: View[] = ["front", "side", "backView"];
const allViews = Object.keys(views) as View[];
const activeStatuses = new Set(["queued", "running", "retrying", "cancelling"]);

interface Brief { description: string; appearance: string }
function briefOf(asset: AssetItem): Brief {
  return { description: asset.description ?? "", appearance: typeof asset.metadata.appearance === "string" ? asset.metadata.appearance : "" };
}
function isBrief(value: unknown): value is Brief {
  return !!value && typeof value === "object" && "description" in value && typeof value.description === "string"
    && "appearance" in value && typeof value.appearance === "string";
}

function CharacterBrief({ asset, scope, editable, busy, saving, saveLocked, conflict, onReload, onDirty, onSave }: {
  asset: AssetItem; scope: ProductionDraftScope; editable: boolean; busy: boolean; saving: boolean; saveLocked: boolean; conflict: boolean;
  onDirty: (dirty: boolean) => void; onReload: () => void; onSave: (change: Record<string, unknown>, version: number) => void;
}) {
  const { t } = useTurnaroundTranslation();
  const [base, setBase] = useState(() => ({ ...briefOf(asset), version: asset.version }));
  const [draft, setDraft] = useState<Brief>(() => briefOf(asset));
  const dirty = base.description !== draft.description || base.appearance !== draft.appearance;
  const value = dirty ? draft : briefOf(asset);
  const recovery = useRecoverableDraft({ scope, kind: "asset", objectId: `turnaround:${asset.id}`, sourceVersion: String(base.version),
    currentSourceVersion: String(asset.version), value: draft, dirty, validate: isBrief });
  const stale = dirty && base.version !== asset.version;
  const disabled = !editable || busy || !!recovery.pending;
  useEffect(() => {
    onDirty(dirty || !!recovery.pending);
    return () => onDirty(false);
  }, [dirty, recovery.pending, onDirty]);

  function change(field: keyof Brief, text: string) {
    if (!dirty) setBase({ ...briefOf(asset), version: asset.version });
    setDraft({ ...value, [field]: text });
  }
  function reset() {
    const saved = briefOf(asset);
    setBase({ ...saved, version: asset.version });
    setDraft(saved);
    recovery.clear();
  }
  function rebase() {
    const saved = briefOf(asset);
    setDraft({ description: draft.description === base.description ? saved.description : draft.description,
      appearance: draft.appearance === base.appearance ? saved.appearance : draft.appearance });
    setBase({ ...saved, version: asset.version });
  }
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  return <section className="xj-turn-panel xj-turn-brief">
    <header><h2><ClipboardPenLine aria-hidden="true" />{t("profile")}</h2>{version && <span>{t("version", { number: version.versionNo })}</span>}</header>
    <div className="xj-turn-body">
      {recovery.pending && <DraftRecoveryNotice busy={busy} canResume={editable && recovery.canResume} value={recovery.pending.value}
        onResume={() => recovery.resume((restored) => { setBase({ ...briefOf(asset), version: asset.version }); setDraft(restored); })} onDiscard={reset} />}
      <label>{t("brief")}<textarea rows={4} value={value.description} readOnly={disabled} placeholder={t("briefHint")} onChange={(event) => change("description", event.target.value)} /></label>
      <label>{t("appearance")}<textarea rows={2} value={value.appearance} readOnly={disabled} placeholder={t("appearanceHint")} onChange={(event) => change("appearance", event.target.value)} /></label>
      {(conflict || stale) && <div className="xj-turn-notice" role="status">{t("staleDraft")} {stale
        ? <><details><summary>{t("compare")}</summary><p>{briefOf(asset).description}</p><p>{briefOf(asset).appearance}</p></details><button type="button" onClick={rebase} disabled={disabled}>{t("rebase")}</button></>
        : <button type="button" onClick={onReload} disabled={busy}>{t("load")}</button>}</div>}
      {editable ? <div className="xj-turn-save">
        <button type="button" className="xj-turn-secondary" disabled={disabled || saveLocked || !dirty || stale} onClick={() => onSave({
          ...(base.description !== draft.description ? { description: draft.description } : {}),
          ...(base.appearance !== draft.appearance ? { metadata: { appearance: draft.appearance } } : {}),
        }, base.version)}><Save size={15} />{t(saving ? "saving" : "save")}</button>
        {dirty ? <button type="button" className="xj-turn-link" disabled={busy} onClick={reset}>{t("undo")}</button> : value.description || value.appearance ? <span><Check size={14} />{t("saved")}</span> : null}
      </div> : <p>{t("readOnly")}</p>}
    </div>
  </section>;
}

export function CharacterTurnaroundStudio({ asset, scope, generation, task, historyState, reloadHistory, canEdit, busy, dirty, reset, conflict,
  onSave, onDirty, onReload, onGenerate, onAdopt }: {
  asset: AssetItem; scope: ProductionDraftScope; generation: AssetImageControls; task?: TaskReceipt;
  historyState: "checking" | "ready" | "failed"; reloadHistory: () => void; canEdit: boolean; busy: boolean; dirty: boolean; reset: number; conflict: boolean;
  onSave: (change: Record<string, unknown>, version: number) => void; onDirty: (dirty: boolean) => void; onReload: () => void;
  onGenerate: (action: "generateReferenceImage" | "generateTurnaround", payload: Record<string, unknown>) => void; onAdopt: (id: string) => void;
}) {
  const { t } = useTurnaroundTranslation();
  const { t: gen } = useAssetGenerationTranslation();
  const [selected, setSelected] = useState<View[]>(basicViews);
  const [direction, setDirection] = useState("");
  const [customView, setCustomView] = useState("");
  const [chosenStage, setChosenStage] = useState<Stage>();
  const [inspection, setInspection] = useState<{ task: string; id: string | null }>();
  const { models } = generation;
  const projectId = asset.projectId ?? models.projectId;
  const history = asset.versions.filter((version) => version.generatedMediaType === "image" && version.generatedAssetId).reverse();
  const adopted = new Set(history.map((version) => version.generatedAssetId));
  const current = history.find((version) => version.id === asset.currentVersionId);
  function capabilityOf(version?: AssetVersion) {
    return version?.imageCapability ?? (version?.generatedAssetId && task?.outputAssetIds?.includes(version.generatedAssetId) ? task.capability : undefined);
  }
  const sheet = capabilityOf(current) === "character_turnaround" ? current : history.find((version) => capabilityOf(version) === "character_turnaround");
  const reference = capabilityOf(current) === "image_generation" ? current : history.find((version) => capabilityOf(version) === "image_generation")
    ?? history.find((version) => !capabilityOf(version)) ?? current;
  const hasReference = assetHasReferenceImage(asset);
  const taskStage = task?.capability === "character_turnaround" ? "sheet" : task?.capability === "image_generation" ? "reference" : undefined;
  const taskKind = t(taskStage ?? (task?.capability === "character_expressions" ? "expressions" : "image"));
  const outputs = task?.status === "succeeded" ? task.outputAssetIds ?? [] : [];
  const fresh = outputs.find((id) => !adopted.has(id));
  const stage = chosenStage ?? (fresh && taskStage ? taskStage : "sheet");
  const drawingReference = stage === "reference" || !hasReference;
  const capability = drawingReference ? "image_generation" : "character_turnaround";
  const action = drawingReference ? "generateReferenceImage" : "generateTurnaround";
  const requestedViews = [...new Set([...allViews.filter((key) => selected.includes(key)).map((key) => views[key]), ...customView.split("\n").map((item) => item.trim()).filter(Boolean)])];
  const available = models.supports(capability);
  const working = !!task && activeStatuses.has(task.status);
  const taskLink = projectId && task ? `/app/workspaces/${encodeURIComponent(models.workspaceId)}/projects/${encodeURIComponent(projectId)}/tasks?taskId=${encodeURIComponent(task.taskId)}` : undefined;
  const recoveryBusy = historyState === "checking" || generation.pending;
  const blocked = !canEdit || busy || dirty || recoveryBusy || historyState === "failed" || !asset.currentVersionId;
  const historyImage = stage === "sheet" ? sheet : reference;
  const candidate = task && stage === taskStage ? (inspection?.task === task.taskId ? inspection.id : fresh) : undefined;
  const shown = candidate ?? historyImage?.generatedAssetId;
  const source = shown && projectId ? `/api/v1/generation-assets/${encodeURIComponent(shown)}/content?projectId=${encodeURIComponent(projectId)}` : undefined;
  const oldCandidate = Boolean(candidate && task?.sourceVersionId && task.sourceVersionId !== asset.currentVersionId && !adopted.has(candidate));
  function generate() {
    setInspection(undefined);
    setChosenStage(undefined);
    onGenerate(action, { sourceVersionId: asset.currentVersionId, ...(!drawingReference ? {
      items: requestedViews, direction: direction.trim(),
    } : {}) });
  }
  return <div className="xj-turn-studio">
    <div className="xj-turn-board-column">
      <section className="xj-turn-panel xj-turn-board">
        <header><h2><Box aria-hidden="true" />{t("canvas")}</h2><div className="xj-turn-switch" role="group" aria-label={t("canvas")}>
          {(["reference", "sheet"] as const).map((key) => <button type="button" key={key} aria-pressed={stage === key} onClick={() => setChosenStage(key)}>{t(key)}</button>)}
        </div></header>
        <figure>
          <div className="xj-turn-canvas">
            <span className="xj-turn-legend">{t(!shown ? "template" : candidate && !adopted.has(candidate) ? "candidate" : "adopted")}</span>
            {source && projectId ? <ReferencePreview key={source} source={source} workspaceId={models.workspaceId} projectId={projectId} className="xj-turn-artwork"
              alt={t("imageAlt", { name: asset.name, kind: t(stage) })} /> : <div className="xj-turn-guide"><img src="/character-turnaround-guide.webp" alt={t("templateAlt")} />
              <div aria-hidden="true">{basicViews.map((v) => <span key={v}>{t(v)}</span>)}</div></div>}
          </div>
          <figcaption><strong>{shown ? asset.name : t("guideCaption")}</strong><span>{shown ? t(candidate && !adopted.has(candidate) ? "review" : stage === "sheet" ? "sheetVersion" : capabilityOf(reference) === "image_generation" ? "refVersion" : "current") : t("templateHint", { name: asset.name })}</span></figcaption>
        </figure>
        {stage === taskStage && outputs.length > 0 && fresh && task ? <div className="xj-turn-review">
          {(outputs.length > 1 || historyImage) && <div className="xj-turn-choices">
            {historyImage && <button type="button" aria-pressed={!candidate} onClick={() => setInspection({ task: task.taskId, id: null })}>{t("original")}</button>}
            {outputs.map((id, index) => <button type="button" key={id} aria-pressed={candidate === id} onClick={() => setInspection({ task: task.taskId, id })}>{outputs.length === 1 ? t("showCandidate") : t("candidateNumber", { number: index + 1 })}</button>)}
          </div>}
          {candidate && !adopted.has(candidate) && <>{oldCandidate && <p>{t("prior")}</p>}
            <button type="button" className="xj-turn-primary" disabled={blocked} onClick={() => onAdopt(candidate)}><Check size={16} />
              {t(generation.activeAction === "adoptGenerationOutput" ? "savingImage" : oldCandidate ? "usePrior" : stage === "sheet" ? "adoptSheet" : "adoptRef")}</button></>}
        </div> : null}
      </section>
      {task && <section className="xj-turn-task" aria-live="polite"><Images aria-hidden="true" size={18} /><div>
        <strong>{taskKind}</strong><p>{task.waitingStopped && working ? t("waitStopped") : task.executionUnconfirmed && working ? t("unknown") : working ? t(task.status === "queued" ? "queued" : task.status === "cancelling" ? "stopping" : "waiting", { kind: taskKind }) :
          task.status !== "succeeded" ? t("failed") : !outputs.length ? t("emptyResult") : fresh ? t("review") : t("adopted")}</p>
        {fresh && taskStage && stage !== taskStage && <button type="button" className="xj-turn-link" onClick={() => { setChosenStage(taskStage); setInspection(undefined); }}>{t("showCandidate")}</button>}
      </div>{taskLink && <a href={taskLink}>{t("task")} <ArrowRight size={13} /></a>}</section>}
    </div>
    <div className="xj-turn-controls">
      <CharacterBrief key={reset} asset={asset} scope={scope} editable={canEdit} busy={busy} saving={generation.activeAction === "updateAsset"} saveLocked={generation.pending} conflict={conflict}
        onReload={onReload} onSave={onSave} onDirty={onDirty} />
      <section className="xj-turn-panel"><header><h2><Layers3 aria-hidden="true" />{t("layout")}</h2></header>
        <div className="xj-turn-body">
          {stage === "sheet" && <><fieldset><legend>{t("angles")}</legend><div className="xj-turn-choices">
            {allViews.map((v) => <button type="button" key={v} aria-pressed={selected.includes(v)} onClick={() => setSelected((list) => list.includes(v) ? list.filter((item) => item !== v) : [...list, v])}>
              {selected.includes(v) && <Check size={13} aria-hidden="true" />}{t(v)}</button>)}
          </div></fieldset><details className="xj-turn-custom"><summary>{t("custom")}</summary><label>{t("customHint")}
            <textarea rows={2} maxLength={500} value={customView} onChange={(event) => setCustomView(event.target.value)} /></label></details>
            <label>{t("direction")}<textarea rows={2} value={direction} maxLength={1000} placeholder={t("directionHint")} onChange={(event) => setDirection(event.target.value)} /></label></>}
          <p>{t(stage === "reference" ? "refInstructions" : !hasReference ? "refFirst" : sheet ? "approvedNext" : "refNext")}</p>
          {dirty && <p className="xj-turn-notice" role="status">{t("saveFirst")}</p>}
          {historyState !== "ready" && <p role="status">{t(historyState === "checking" ? "pending" : "lookupFailed")} {historyState === "failed" && <button type="button" className="xj-turn-link" onClick={reloadHistory}>{t("retry")}</button>}</p>}
          {!drawingReference && !requestedViews.length && <p role="status">{t("selectOne")}</p>}
          {generation.rejection && <p role="status" className="xj-turn-notice">{generation.rejection}</p>}
          {!available && <p role="status">{gen(models.notice ?? (drawingReference ? "modelMissing" : "noDerivative"))} {models.notice !== "checking" && <button type="button" className="xj-turn-link" onClick={models.recheck}>{t("retry")}</button>}</p>}
          <AssetImageBilling key={capability} generation={generation} capability={capability} />
          <button type="button" className="xj-turn-primary xj-turn-generate" disabled={blocked || working || !available || (!drawingReference && !requestedViews.length)} onClick={generate}>
            {drawingReference ? <ScanFace size={17} /> : <Sparkles size={17} />}{t(working ? task?.waitingStopped ? "awaitReview" : task?.executionUnconfirmed ? "awaitResult" : "making" : generation.activeAction === action ? "busy" : drawingReference ? hasReference ? "redoRef" : "generateRef" : sheet ? "redoSheet" : "generateSheet", { kind: taskKind })}
          </button>
          {stage === "reference" && hasReference && <button type="button" className="xj-turn-link" onClick={() => setChosenStage("sheet")}>{t("nextSheet")} <ArrowRight size={15} /></button>}
        </div>
      </section>
    </div>
  </div>;
}
