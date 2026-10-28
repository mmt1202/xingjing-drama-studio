import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSetupTranslation } from "./setup-i18n";
import { useControlTranslation } from "./control-i18n";
import type { ProjectSetup, ProjectSetupOptions, SessionContext } from "./types";

export const setupInput = "mt-2 w-full rounded-xl border border-[#343845] bg-[#0d1018] px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-400/20 disabled:opacity-50";
export const setupButton = "rounded-xl border border-[#343845] px-4 py-2.5 text-sm transition hover:border-orange-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-orange-400 disabled:cursor-wait disabled:opacity-50";

function Field({ label, children, help }: { label: string; children: ReactNode; help?: string }) {
  return <label className="block text-sm font-medium text-slate-200"><span>{label}</span>{children}{help && <span className="mt-2 block text-xs font-normal leading-5 text-slate-400">{help}</span>}</label>;
}

export function ProjectSetupFields({ step, name, onName, value, patch, context, options, invalidFields, onReadingFile }: {
  step: number; name: string; onName: (name: string) => void; value: ProjectSetup;
  patch: (patch: Partial<ProjectSetup>) => void; context: SessionContext; options?: ProjectSetupOptions; invalidFields: string[];
  onReadingFile: (reading: boolean) => void;
}) {
  const { t } = useSetupTranslation();
  const { t: controlText } = useControlTranslation();
  const [fileError, setFileError] = useState(false);
  const readSequence = useRef(0);
  useEffect(() => () => { readSequence.current++; }, []);
  const select = <K extends keyof ProjectSetup>(key: K, values: string[], help?: string) => <Field label={t(key)} help={help}>
    <select className={setupInput} value={String(value[key])} aria-invalid={invalidFields.includes(key)} onChange={(event) => {
      const next = event.target.value;
      patch({ [key]: next, ...(key === "projectType" && next === "brand" ? { episodeCount: 1 } : {}),
        ...(key === "primaryPlatform" ? { secondaryPlatforms: value.secondaryPlatforms.filter((platform) => platform !== next) } : {}),
        ...(key === "sourceKind" && ["novel", "script"].includes(next) ? { rightsBasis: "pending" } : {}),
      });
    }}>{values.map((option) => <option key={option} value={option}>{option.includes(":") ? option : t(option)}</option>)}</select>
  </Field>;
  const text = (key: "shortName" | "directorStyle" | "visualStyle" | "rightsNote", maxLength: number) => <Field label={t(key)}>
    <input className={setupInput} maxLength={maxLength} value={value[key]} aria-invalid={invalidFields.includes(key)} onChange={(event) => patch({ [key]: event.target.value })} />
  </Field>;
  const number = (key: "episodeCount" | "episodeDurationSeconds" | "candidateCount" | "teamSeats" | "reviewSeats" | "internalReviewRounds" | "clientReviewRounds" | "scheduleDays", min: number, max: number) => <Field label={t(key)}>
    <input type="number" min={min} max={max} step={1} className={setupInput} disabled={key === "episodeCount" && value.projectType === "brand"}
      value={value[key] ?? ""} placeholder={key === "scheduleDays" ? t("optional") : undefined} aria-invalid={invalidFields.includes(key)}
      onChange={(event) => patch({ [key]: event.target.value === "" && key === "scheduleDays" ? null : Number(event.target.value) })} />
  </Field>;
  const languages = ["zh", "en", "vi"] as const;
  const applyPreset = (preset: ProjectSetup["preset"]) => {
    patch({ preset, ...(preset ? {
      projectType: preset === "brand" ? "brand" : preset === "comic" ? "comic" : "drama",
      episodeCount: preset === "brand" ? 1 : 12, candidateCount: preset === "suspense" ? 6 : 3,
      visualStyle: t(`style_${preset}`), deliveryType: preset === "brand" ? "proposal" : "final",
      clientCanDownload: false, internalReviewRounds: 2, clientReviewRounds: 1,
    } : {}) });
  };
  async function readFile(file?: File) {
    const sequence = ++readSequence.current;
    if (!file) return;
    setFileError(false);
    onReadingFile(true);
    try {
      if (!file.name.toLowerCase().endsWith(".txt") || file.size > 3_000_000) throw new Error("invalid");
      const content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      if (content.length > 1_000_000 || content.includes("\0")) throw new Error("invalid");
      if (readSequence.current === sequence) patch({ sourceText: content });
    } catch { if (readSequence.current === sequence) setFileError(true); }
    finally { if (readSequence.current === sequence) onReadingFile(false); }
  }
  return <div className="space-y-6">
    {step === 0 && <>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label={t("name")}><input required maxLength={255} className={setupInput} value={name} aria-invalid={invalidFields.includes("name")} onChange={(event) => onName(event.target.value)} /></Field>
        {text("shortName", 64)}
        <Field label={t("ownerId")}><select className={setupInput} value={value.ownerId} aria-invalid={invalidFields.includes("ownerId")} onChange={(event) => patch({ ownerId: event.target.value })}>
          <option value={context.user.id}>{context.user.name}</option>
          {options?.members.filter((member) => member.id !== context.user.id).map((member) => <option key={member.id} value={member.id}>{member.name}</option>)}
          {value.ownerId !== context.user.id && !options?.members.some((member) => member.id === value.ownerId) && <option value={value.ownerId}>{controlText("ownerNameUnavailable")}</option>}
        </select></Field>
        <Field label={t("workspace")}><input className={setupInput} readOnly value={context.workspace.name} /></Field>
        {select("projectType", ["drama", "comic", "brand", "adaptation"])}
        {select("sourceKind", ["blank", "novel", "script", "template"])}
      </div>
      {value.sourceKind === "template" && <Field label={t("preset")} help={t("presetHelp")}><select className={setupInput} value={value.preset} aria-invalid={invalidFields.includes("preset")} onChange={(event) => applyPreset(event.target.value as ProjectSetup["preset"])}>
        <option value="">{t("missingPreset")}</option>{["suspense", "comic", "brand"].map((preset) => <option key={preset} value={preset}>{t(`preset_${preset}`)}</option>)}
      </select></Field>}
      {["novel", "script"].includes(value.sourceKind) && <div className="space-y-3">
        <Field label={t("sourceText")} help={t("sourceHelp")}><textarea className={setupInput + " min-h-52 leading-6"} maxLength={1_000_000} value={value.sourceText} aria-invalid={invalidFields.includes("sourceText")} onChange={(event) => { readSequence.current++; patch({ sourceText: event.target.value }); }} /></Field>
        <Field label={t("readFile")}><input className={setupInput} type="file" accept=".txt,text/plain" onChange={(event) => { void readFile(event.target.files?.[0]); event.target.value = ""; }} /></Field>
        {fileError && <p role="alert" className="text-sm text-rose-300">{t("fileError")}</p>}
      </div>}
    </>}
    {step === 1 && <div className="grid gap-5 sm:grid-cols-2">
      {number("episodeCount", 1, 200)}{number("episodeDurationSeconds", 5, 7200)}
      {select("primaryPlatform", ["douyin", "kuaishou", "wechat", "bilibili", "web"])}{select("aspectRatio", ["9:16", "16:9", "1:1"])}
      <fieldset className="sm:col-span-2"><legend className="text-sm font-medium">{t("secondaryPlatforms")}</legend><div className="mt-3 flex flex-wrap gap-4">{(["douyin", "kuaishou", "wechat", "bilibili", "web"] as const).filter((platform) => platform !== value.primaryPlatform).map((platform) => <label key={platform} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={value.secondaryPlatforms.includes(platform)} onChange={(event) => patch({ secondaryPlatforms: event.target.checked ? [...value.secondaryPlatforms, platform] : value.secondaryPlatforms.filter((item) => item !== platform) })} />{t(platform)}</label>)}</div></fieldset>
      {select("deliveryType", ["final", "proposal"])}{select("generationMode", ["storyboard", "reference_video"], t("immutable"))}
      {text("directorStyle", 200)}{text("visualStyle", 1000)}
      {select("modelStrategy", ["balanced", "quality", "economy"])}{number("candidateCount", 1, 12)}
      {(["image", "video"] as const).map((kind) => { const key = `${kind}ModelKey` as const; return <Field key={key} label={t(key)}><select className={setupInput} value={value[key]} aria-invalid={invalidFields.includes(key)} onChange={(event) => patch({ [key]: event.target.value })}>
        <option value="">{t("automatic")}</option>{options?.models.filter((model) => model.mediaTypes.includes(kind)).map((model) => <option key={model.key} value={model.key}>{model.name}</option>)}
        {value[key] && !options?.models.some((model) => model.key === value[key]) && <option value={value[key]}>{value[key]}</option>}
      </select></Field>; })}
      <fieldset><legend className="text-sm font-medium">{t("subtitleLanguages")}</legend><div className="mt-3 flex flex-wrap gap-4">{languages.map((language) => <label key={language} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={value.subtitleLanguages.includes(language)} onChange={(event) => patch({ subtitleLanguages: event.target.checked ? [...value.subtitleLanguages, language] : value.subtitleLanguages.filter((item) => item !== language) })} />{t(language)}</label>)}</div></fieldset>
      {select("reviewLanguage", [...languages])}
    </div>}
    {step === 2 && <>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label={t("budgetMinor")}><input className={setupInput} type="number" min="0.01" max="1000000000" step="0.01" placeholder={t("optional")} value={value.budgetMinor === null ? "" : value.budgetMinor / 100} aria-invalid={invalidFields.includes("budgetMinor")} onChange={(event) => patch({ budgetMinor: event.target.value === "" ? null : Math.round(Number(event.target.value) * 100) })} /></Field>
        {number("scheduleDays", 1, 3650)}{number("teamSeats", 1, 1000)}{number("reviewSeats", 1, 1000)}
        <p className="text-xs leading-5 text-slate-400 sm:col-span-2">{t("seatHelp")}</p>
        {number("internalReviewRounds", 1, 20)}{number("clientReviewRounds", 0, 20)}
      </div>
      <fieldset><legend className="text-sm font-medium">{t("clientPermissions")}</legend><div className="mt-3 flex flex-wrap gap-4">{(["clientCanComment", "clientCanDownload", "clientCanApprove"] as const).map((key) => <label key={key} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={value[key]} onChange={(event) => patch({ [key]: event.target.checked })} />{t(key)}</label>)}</div></fieldset>
      <div className="grid gap-5 sm:grid-cols-2">{select("rightsBasis", ["original", "licensed", "pending"], t("rightsHelp"))}{text("rightsNote", 2000)}</div>
    </>}
  </div>;
}
