import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Sparkles } from "lucide-react";
import { AssetsStoryboardApiError } from "./api";
import { profileDetails, profileLabel, useAssetProfileTranslation } from "./asset-profile-i18n";
import { useAssetEditorTranslation } from "./editor-i18n";
import type { AssetItem, AssetProfileDraft, AssetsStoryboardPort, WorkspaceProjectScope } from "./contracts";
import { button } from "./ui";

export const profileInput = "mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA] focus-visible:outline-2 focus-visible:outline-violet-400";

type Props = {
  asset: AssetItem; scope: WorkspaceProjectScope; api: AssetsStoryboardPort; disabled: boolean;
  values: Record<string, string>; onApply: (fields: Record<string, string>) => void;
};

export function AssetProfileSuggestions({ asset, scope, api, disabled, values, onApply }: Props) {
  const { t } = useAssetProfileTranslation();
  const { t: editor } = useAssetEditorTranslation();
  const [direction, setDirection] = useState("");
  const [result, setResult] = useState<AssetProfileDraft>();
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<"fail" | "progress" | "stale" | "invalid" | "denied">();
  const [applied, setApplied] = useState(0);
  const [working, setWorking] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const fields = ["name", "description", ...profileDetails[asset.type]];
  const proposed = result && result.assetVersion === asset.version
    ? fields.filter((key) => result.fields[key]?.trim() && result.fields[key] !== values[key]?.trim()) : [];
  const chosen = proposed.filter((key) => selected[key] === values[key]);

  async function generate() {
    if (controller.current || disabled) return;
    const request = new AbortController();
    controller.current = request;
    setWorking(true);
    setNotice(undefined);
    setApplied(0);
    try {
      const response = await api.assetAction(scope, {
        action: "draftAssetProfile", targetId: asset.id, version: asset.version,
        payload: { ownerProjectId: asset.projectId, fields: values, direction: direction.trim() },
      }, request.signal);
      if (request.signal.aborted) return;
      const draft = response.profileDraft;
      if (!draft?.fields || draft.assetVersion !== asset.version) throw new Error("Missing asset draft");
      setResult(draft);
      setSelected(Object.fromEntries(fields.filter((key) => !values[key]?.trim()).map((key) => [key, values[key]])));
    } catch (error) {
      if (request.signal.aborted) return;
      const code = error instanceof AssetsStoryboardApiError ? error.code : "";
      const status = error instanceof AssetsStoryboardApiError ? error.status : undefined;
      setNotice(code === "ASSET_PROFILE_IN_PROGRESS" ? "progress" : code === "ASSET_PROFILE_INPUT_INVALID" ? "invalid"
        : status === 409 ? "stale" : status === 403 || status === 404 ? "denied" : "fail");
    } finally {
      if (!request.signal.aborted) { controller.current = null; setWorking(false); }
    }
  }

  function apply() {
    if (!result || disabled || !chosen.length) return;
    onApply(Object.fromEntries(chosen.map((key) => [key, result.fields[key]])));
    setApplied(chosen.length);
    setResult(undefined);
  }

  return <section aria-label={t("title")} className="mt-5 rounded-2xl border border-violet-400/30 bg-violet-400/[0.06] p-4 sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="flex items-center gap-2 font-semibold"><Sparkles aria-hidden="true" size={18} className="text-violet-300" />{t("title")}</h3>
        <p className="mt-1.5 text-sm leading-6 text-slate-300">{t("intro")}</p></div>
      <button type="button" disabled={disabled || working} onClick={() => void generate()}
        className={button + " inline-flex min-h-10 items-center gap-2 border-violet-400/60 bg-violet-500/20 text-violet-100"}>
        {working ? <LoaderCircle size={16} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : null}
        {t(working ? "working" : result || applied ? "again" : "generate")}
      </button>
    </div>
    <details className="mt-3 text-sm text-slate-300">
      <summary className="w-fit cursor-pointer rounded focus-visible:outline-2 focus-visible:outline-violet-400">{t("direction")}</summary>
      <textarea aria-label={t("direction")} placeholder={t("directionHint")} maxLength={600} rows={2} value={direction}
        onChange={(event) => setDirection(event.target.value)} disabled={working || disabled} className={profileInput} />
    </details>
    <div role="status" aria-live="polite">
      {working ? <span className="sr-only">{t("working")}</span> : null}
      {notice ? <p className="mt-3 text-sm text-amber-200">{t(notice)}</p> : null}
      {applied > 0 ? <p className="mt-3 flex items-center gap-2 text-sm text-emerald-200"><Check size={16} aria-hidden="true" />{t("applied", { count: applied })}</p> : null}
    </div>
    {result ? <fieldset disabled={disabled || working} className="mt-4 min-w-0 border-t border-white/10 pt-3">
      <legend className="sr-only">{t("review")}</legend>
      <h4 className="font-semibold">{t("review")}</h4>
      <p className="mt-1 text-sm leading-6 text-slate-300">{t(result.usedScript ? "fromScript" : "fromAsset")} {t(proposed.length ? "reviewHint" : "same")}</p>
      {proposed.map((key) => <label key={key} className="mt-2 flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-[#12141b] p-3 text-sm has-checked:border-violet-400/60">
        <input type="checkbox" checked={chosen.includes(key)} className="mt-1 size-4 shrink-0 accent-violet-500"
          onChange={(event) => {
            const next = { ...selected };
            if (event.target.checked) next[key] = values[key]; else delete next[key];
            setSelected(next);
          }} />
        <span className="min-w-0 break-words"><strong className="block text-violet-200">{key === "name" ? editor(asset.type + "Name") : key === "description" ? editor(asset.type + "Brief") : t(profileLabel(asset.type, key))}</strong>
          <span className="mt-1 block leading-6 text-slate-100">{result.fields[key]}</span>
          {values[key]?.trim() ? <span className="mt-1 block text-xs leading-5 text-slate-400">{t("current")} · {values[key]}</span> : null}
        </span>
      </label>)}
      <div className="mt-3 flex flex-wrap gap-2">
        {proposed.length ? <button type="button" className={button + " bg-violet-600"} disabled={!chosen.length} onClick={apply}>{t("apply", { count: chosen.length })}</button> : null}
        <button type="button" className={button} onClick={() => setResult(undefined)}>{t("dismiss")}</button>
      </div>
    </fieldset> : null}
  </section>;
}
