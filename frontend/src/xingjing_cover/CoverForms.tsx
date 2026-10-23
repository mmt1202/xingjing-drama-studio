import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { LoaderCircle, Save } from "lucide-react";
import { useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import type { CoverCandidate, CoverCatalog, CoverPlan, CoverVariant, PlanInput, VariantInput } from "./contracts";
import { coverButton, coverField, coverPanel, useCoverTranslation } from "./i18n";
import "./cover.css";

function useDraftGuard(blocked: boolean) {
  useProjectNavigationBlocker(blocked);
  useEffect(() => {
    if (!blocked) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [blocked]);
}

function useSave(busy: boolean) {
  const inFlight = useRef(false);
  const [working, setWorking] = useState(false);
  const [errorKey, setErrorKey] = useState<string>();
  async function save(work: () => Promise<boolean>) {
    if (busy || inFlight.current) return;
    inFlight.current = true;
    setWorking(true);
    setErrorKey(undefined);
    try {
      if (!await work()) setErrorKey("saveFailed");
    } catch {
      setErrorKey("failed");
    } finally {
      inFlight.current = false;
      setWorking(false);
    }
  }
  return { working, errorKey, setErrorKey, save };
}

function SaveActions({ disabled, working, onCancel }: { disabled: boolean; working: boolean; onCancel: () => void }) {
  const { t } = useCoverTranslation();
  return <div className="xj-cover-form-actions">
    <button type="submit" className={`${coverButton} xj-cover-button--primary`} disabled={disabled}>
      {working ? <LoaderCircle size={15} aria-hidden="true" className="animate-spin" /> : <Save size={15} aria-hidden="true" />}
      {t(working ? "saving" : "save")}
    </button>
    <button type="button" className={coverButton} disabled={working} onClick={onCancel}>{t("cancel")}</button>
  </div>;
}

export function PlanForm({ catalog, plan, initialFinalVersionId, initialTarget, busy, onSave, onCancel }: {
  catalog: CoverCatalog; plan?: CoverPlan; initialFinalVersionId?: string; initialTarget?: string; busy: boolean;
  onSave: (input: PlanInput) => Promise<boolean>; onCancel: () => void;
}) {
  const { t } = useCoverTranslation();
  const headingId = useId();
  const safeHelpId = useId();
  const [initial] = useState(() => ({
    name: plan?.name ?? "", finalVersionId: plan?.finalVersionId ?? initialFinalVersionId ?? "",
    target: plan?.target ?? (initialTarget && catalog.targets.includes(initialTarget) ? initialTarget : ""),
    prompt: plan?.prompt ?? "", aspectRatio: plan?.aspectRatio ?? catalog.aspectRatios[0] ?? "",
    x: String((plan?.titleSafeArea.x ?? 0) * 100), y: String((plan?.titleSafeArea.y ?? 0) * 100),
    width: String((plan?.titleSafeArea.width ?? 1) * 100), height: String((plan?.titleSafeArea.height ?? 1) * 100),
  }));
  const [draft, setDraft] = useState(initial);
  const { working, errorKey, setErrorKey, save } = useSave(busy);
  const disabled = busy || working;
  useDraftGuard(disabled || JSON.stringify(draft) !== JSON.stringify(initial));
  const currentFinal = catalog.finalVideos.find((item) => item.id === draft.finalVersionId);
  function field(key: keyof typeof draft, value: string) { setDraft((previous) => ({ ...previous, [key]: value })); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || !catalog.permissions.canManage) return;
    const { name, prompt, aspectRatio } = draft;
    const finalVersionId = plan?.finalVersionId ?? draft.finalVersionId;
    const target = plan?.target ?? draft.target;
    if (!name.trim() || name.trim().length > 160 || !prompt.trim() || prompt.trim().length > 12000
      || (!plan && (!currentFinal || !catalog.targets.includes(target))) || !catalog.aspectRatios.includes(aspectRatio)) {
      setErrorKey("invalidPlan"); return;
    }
    const values = [draft.x, draft.y, draft.width, draft.height];
    const [x, y, width, height] = values.map(Number);
    if (values.some((value) => !value.trim()) || ![x, y, width, height].every(Number.isFinite)
      || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 100 || y + height > 100) {
      setErrorKey("invalidSafeArea"); return;
    }
    await save(() => onSave({ name: name.trim(), prompt: prompt.trim(), finalVersionId, target, aspectRatio,
      titleSafeArea: { x: x / 100, y: y / 100, width: width / 100, height: height / 100 } }));
  }
  return <form className={`${coverPanel} xj-cover-form xj-cover-form--active`} aria-labelledby={headingId} onSubmit={(event) => void submit(event)} noValidate>
    <div><h2 id={headingId} className="xj-cover-form-title">{t(plan ? "editPlan" : "createPlan")}</h2><p className="xj-cover-help">{t("planHelp")}</p></div>
    <fieldset className="xj-cover-fieldset" disabled={disabled}>
      <label className="xj-cover-label">{t("name")}<input className={coverField} value={draft.name} onChange={(event) => field("name", event.target.value)} required maxLength={160} placeholder={t("namePlaceholder")} /></label>
      <div className="xj-cover-fields">
        <label className="xj-cover-label">{t("finalVersion")}
          {plan ? <input className={coverField} readOnly value={currentFinal ? t("finalOption", { episode: currentFinal.episodeId, revision: currentFinal.revision }) : `${t("finalUnavailable")} · ${plan.finalVersionId}`} />
            : <select className={coverField} value={draft.finalVersionId} onChange={(event) => field("finalVersionId", event.target.value)} required><option value="">{t("chooseFinal")}</option>{draft.finalVersionId && !currentFinal ? <option value={draft.finalVersionId} disabled>{t("finalUnavailable")} · {draft.finalVersionId}</option> : null}{catalog.finalVideos.map((item) => <option key={item.id} value={item.id}>{t("finalOption", { episode: item.episodeId, revision: item.revision })}</option>)}</select>}
        </label>
        <label className="xj-cover-label">{t("target")}
          {plan ? <input className={coverField} readOnly value={t(`platform.${plan.target}`, { defaultValue: plan.target })} />
            : <select className={coverField} value={draft.target} onChange={(event) => field("target", event.target.value)} required><option value="">{t("chooseTarget")}</option>{catalog.targets.map((target) => <option key={target} value={target}>{t(`platform.${target}`, { defaultValue: target })}</option>)}</select>}
        </label>
      </div>
      {plan ? <p className="xj-cover-help">{t("immutableScope")}</p> : catalog.finalVideos.length === 0 ? <p className="xj-cover-help">{t("emptyFinals")}</p> : null}
      <label className="xj-cover-label">{t("prompt")}<textarea className={`${coverField} xj-cover-textarea`} value={draft.prompt} onChange={(event) => field("prompt", event.target.value)} maxLength={12000} required placeholder={t("promptPlaceholder")} /></label>
      <label className="xj-cover-label">{t("aspectRatio")}<select className={coverField} value={draft.aspectRatio} onChange={(event) => field("aspectRatio", event.target.value)} required><option value="">{t("chooseAspectRatio")}</option>{catalog.aspectRatios.map((ratio) => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
      <div className="xj-cover-form-section"><h3 className="xj-cover-form-title">{t("titleSafeArea")}</h3><p id={safeHelpId} className="xj-cover-help">{t("safeAreaHelp")}</p>
        <div className="xj-cover-fields">{(["x", "y", "width", "height"] as const).map((key) => <label key={key} className="xj-cover-label">{t({ x: "safeX", y: "safeY", width: "safeWidth", height: "safeHeight" }[key])}<input className={coverField} type="number" min="0" max="100" step="any" inputMode="decimal" required value={draft[key]} onChange={(event) => field(key, event.target.value)} aria-describedby={safeHelpId} /></label>)}</div>
      </div>
    </fieldset>
    {errorKey ? <p role="alert" className="xj-cover-form-error">{t(errorKey)}</p> : null}
    <SaveActions disabled={disabled || !catalog.permissions.canManage || (!plan && !currentFinal)} working={working} onCancel={onCancel} />
  </form>;
}

export function VariantForm({ candidates, variant, initialAssetId, busy, onSave, onCancel }: {
  candidates: CoverCandidate[]; variant?: CoverVariant; initialAssetId?: string; busy: boolean;
  onSave: (input: VariantInput) => Promise<boolean>; onCancel: () => void;
}) {
  const { t } = useCoverTranslation();
  const headingId = useId();
  const tagsHelpId = useId();
  const [initial] = useState(() => ({ label: variant?.label ?? "", coverAssetId: variant?.coverAssetId ?? initialAssetId ?? "",
    title: variant?.title ?? "", description: variant?.description ?? "", tags: variant?.tags.join("\n") ?? "" }));
  const [draft, setDraft] = useState(initial);
  const { working, errorKey, setErrorKey, save } = useSave(busy);
  const disabled = busy || working;
  useDraftGuard(disabled || JSON.stringify(draft) !== JSON.stringify(initial));
  const selectedAvailable = candidates.some((candidate) => candidate.assetId === draft.coverAssetId && candidate.available);
  function field(key: keyof typeof draft, value: string) { setDraft((previous) => ({ ...previous, [key]: value })); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    const label = draft.label.trim(), title = draft.title.trim(), description = draft.description.trim();
    if (!selectedAvailable || !label || label.length > 80 || !title || title.length > 200 || !description || description.length > 3000) {
      setErrorKey("invalidVariant"); return;
    }
    const tags = draft.tags.split(/\r?\n/).map((tag) => tag.trim()).filter(Boolean);
    if (tags.length > 20 || tags.some((tag) => tag.length > 80) || new Set(tags).size !== tags.length) {
      setErrorKey("invalidTags"); return;
    }
    await save(() => onSave({ ...(variant ? { id: variant.id } : {}), label, title, description, tags, coverAssetId: draft.coverAssetId }));
  }
  return <form className={`${coverPanel} xj-cover-form xj-cover-form--active`} aria-labelledby={headingId} onSubmit={(event) => void submit(event)} noValidate>
    <div><h2 id={headingId} className="xj-cover-form-title">{t(variant ? "editVariant" : "addVariant")}</h2><p className="xj-cover-help">{t("variantHelp")}</p></div>
    <fieldset className="xj-cover-fieldset" disabled={disabled}>
      <div className="xj-cover-fields">
        <label className="xj-cover-label">{t("variantLabel")}<input className={coverField} value={draft.label} onChange={(event) => field("label", event.target.value)} required maxLength={80} placeholder={t("variantLabelPlaceholder")} /></label>
        <label className="xj-cover-label">{t("sourceAsset")}<select className={coverField} value={draft.coverAssetId} onChange={(event) => field("coverAssetId", event.target.value)} required>
          <option value="">{t("chooseCandidate")}</option>
          {draft.coverAssetId && !candidates.some((candidate) => candidate.assetId === draft.coverAssetId) ? <option value={draft.coverAssetId} disabled>{t("unavailableCandidate")} · {draft.coverAssetId}</option> : null}
          {candidates.map((candidate) => <option key={candidate.assetId} value={candidate.assetId} disabled={!candidate.available}>{candidate.available ? candidate.assetId : `${t("unavailableCandidate")} · ${candidate.assetId}`}</option>)}
        </select></label>
      </div>
      <label className="xj-cover-label">{t("title")}<textarea className={coverField} rows={2} value={draft.title} onChange={(event) => field("title", event.target.value)} maxLength={200} required placeholder={t("titlePlaceholder")} /></label>
      <label className="xj-cover-label">{t("description")}<textarea className={`${coverField} xj-cover-textarea`} value={draft.description} onChange={(event) => field("description", event.target.value)} maxLength={3000} required placeholder={t("descriptionPlaceholder")} /></label>
      <label className="xj-cover-label">{t("tags")}<textarea className={`${coverField} xj-cover-textarea`} value={draft.tags} onChange={(event) => field("tags", event.target.value)} aria-describedby={tagsHelpId} /></label><p id={tagsHelpId} className="xj-cover-help">{t("tagsHelp")}</p>
    </fieldset>
    {errorKey ? <p role="alert" className="xj-cover-form-error">{t(errorKey)}</p> : null}
    <SaveActions disabled={disabled || !selectedAvailable} working={working} onCancel={onCancel} />
  </form>;
}

export function EvaluationForm({ variant, busy, onSave, onCancel }: {
  variant: CoverVariant; busy: boolean; onSave: (score: number, note: string) => Promise<boolean>; onCancel: () => void;
}) {
  const { t } = useCoverTranslation();
  const headingId = useId();
  const helpId = useId();
  const [initial] = useState(() => ({ score: variant.score === null ? "" : String(variant.score), note: variant.evaluationNote }));
  const [draft, setDraft] = useState(initial);
  const { working, errorKey, setErrorKey, save } = useSave(busy);
  const disabled = busy || working;
  useDraftGuard(disabled || JSON.stringify(draft) !== JSON.stringify(initial));
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    const score = Number(draft.score), note = draft.note.trim();
    if (!draft.score.trim() || !Number.isFinite(score) || score < 0 || score > 100 || !note || note.length > 3000) {
      setErrorKey("invalidEvaluation"); return;
    }
    await save(() => onSave(score, note));
  }
  return <form className={`${coverPanel} xj-cover-form xj-cover-form--active`} aria-labelledby={headingId} onSubmit={(event) => void submit(event)} noValidate>
    <div><h2 id={headingId} className="xj-cover-form-title">{t("manualEvaluation")} · {variant.label}</h2><p id={helpId} className="xj-cover-help">{t("evaluationHelp")}</p></div>
    <fieldset className="xj-cover-fieldset" disabled={disabled}>
      <label className="xj-cover-label">{t("scoreRange")}<input className={coverField} type="number" min="0" max="100" step="any" inputMode="decimal" required value={draft.score} onChange={(event) => setDraft((previous) => ({ ...previous, score: event.target.value }))} aria-describedby={helpId} /></label>
      <label className="xj-cover-label">{t("note")}<textarea className={`${coverField} xj-cover-textarea`} required maxLength={3000} value={draft.note} onChange={(event) => setDraft((previous) => ({ ...previous, note: event.target.value }))} placeholder={t("evaluationPlaceholder")} /></label>
    </fieldset>
    {errorKey ? <p role="alert" className="xj-cover-form-error">{t(errorKey)}</p> : null}
    <SaveActions disabled={disabled} working={working} onCancel={onCancel} />
  </form>;
}
