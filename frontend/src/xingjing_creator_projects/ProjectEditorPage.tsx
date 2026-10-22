import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, Check, Save } from "lucide-react";
import { CreatorApiError } from "./api";
import { CreatorPage } from "./shared";
import { ProjectSetupFields, setupButton } from "./ProjectSetupFields";
import { ProjectSetupPreview } from "./ProjectSetupPreview";
import { useSetupTranslation } from "./setup-i18n";
import { initialSetup, missingSetup } from "./setup-state";
import type { CreatorProjectsPort, ProjectDetail, ProjectSetup, ProjectSetupOptions, SessionContext } from "./types";

interface Props {
  api: CreatorProjectsPort; projectId?: string; mode?: "quick" | "guided";
  onCreated?: (id: string) => void; onDraftSaved?: (id: string) => void; onBack?: () => void;
}

export function ProjectEditorPage(props: Props) {
  const { t } = useSetupTranslation();
  return <CreatorPage api={props.api} permission="project.manage" title={t(props.projectId ? "editDraft" : props.mode === "guided" ? "guided" : "quick")}>
    {(context) => <ProjectSetupForm key={`${context.user.id}:${context.workspace.id}:${props.projectId ?? "new"}`} {...props} context={context} />}
  </CreatorPage>;
}

function ProjectSetupForm({ api, projectId, mode = "quick", onCreated, onDraftSaved, onBack, context }: Props & { context: SessionContext }) {
  const { t } = useSetupTranslation();
  const [name, setName] = useState("");
  const [value, setValue] = useState<ProjectSetup>(() => initialSetup(context.user.id));
  const [project, setProject] = useState<ProjectDetail>();
  const [options, setOptions] = useState<ProjectSetupOptions>();
  const [optionsError, setOptionsError] = useState(false);
  const [loading, setLoading] = useState(Boolean(projectId));
  const [saving, setSaving] = useState(false);
  const [readingFile, setReadingFile] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>();
  const [validation, setValidation] = useState(false);
  const [step, setStep] = useState(0);
  const [reload, setReload] = useState(0);
  const [optionsReload, setOptionsReload] = useState(0);
  const busy = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    if (projectId) {
      api.getProject(projectId).then((item) => { if (active) { setProject(item); setName(item.name); setValue({ ...initialSetup(context.user.id), ...item.configuration }); setSaved(item.setupState === "draft"); setError(undefined); } })
        .catch((problem: unknown) => { if (active) setError(problem); }).finally(() => { if (active) setLoading(false); });
    }
    return () => { active = false; };
  }, [api, projectId, context.user.id, reload]);
  useEffect(() => {
    let active = true;
    if (api.getProjectSetupOptions) void api.getProjectSetupOptions().then((result) => { if (active) { setOptions(result); setOptionsError(false); } }).catch(() => { if (active) setOptionsError(true); });
    return () => { active = false; };
  }, [api, optionsReload]);
  const patch = useCallback((next: Partial<ProjectSetup>) => { setValue((current) => ({ ...current, ...next })); setSaved(false); }, []);
  async function save(draft: boolean) {
    if (busy.current || loading || readingFile || projectId && !project) return;
    if (!name.trim() || !draft && missingSetup(name, value).length) { setValidation(true); setStep(0); return; }
    busy.current = true; setSaving(true); setError(undefined); setValidation(false);
    try {
      const action = draft ? "saveDraft" : project?.setupState === "draft" ? "activate" : "create";
      const next = await api.projectAction(project?.id ?? "new", action, { name: name.trim(), mode, configuration: value }, project?.version);
      if (!alive.current) return;
      setProject(next); setSaved(true);
      if (draft) onDraftSaved?.(next.id); else onCreated?.(next.id);
    } catch (problem) { if (alive.current) setError(problem); }
    finally { busy.current = false; if (alive.current) setSaving(false); }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (readingFile) return;
    if (mode === "guided" && step < 2) { setStep(step + 1); return; }
    void save(false);
  }
  const apiError = error instanceof CreatorApiError ? error : undefined;
  const invalidFields = Array.isArray(apiError?.details?.fields) ? apiError.details.fields.map(String) : [];
  const errorKey = apiError?.status === 409 ? "conflict" : apiError?.status === 403 ? "forbidden" : apiError?.status === 422 ? "invalid" : "error";
  const isReady = project?.setupState === "ready";
  const pending = saving || readingFile;
  return <div className="mx-auto max-w-7xl">
    <div className="mb-6 flex flex-wrap items-center justify-between gap-4"><p className="max-w-2xl text-sm leading-6 text-slate-400">{t("intro")}</p>{onBack && <button type="button" className={setupButton} disabled={pending} onClick={onBack}><ArrowLeft size={15} className="mr-2 inline" />{t("back")}</button>}</div>
    {loading ? <p role="status" className="py-16 text-center text-slate-400">{t("loading")}</p> : <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <form onSubmit={submit} className="min-w-0 space-y-5">
        {isReady ? <section className="rounded-2xl border border-emerald-400/20 p-6"><h2 className="text-lg font-semibold">{t("created")}</h2><p className="mt-3 text-sm text-slate-400">{t("readonly")}</p><button type="button" className={setupButton + " mt-5"} onClick={() => onCreated?.(project.id)}>{t("openProject")}</button></section> : <>
          {mode === "guided" && <nav aria-label={t("guided")} className="grid grid-cols-3 gap-2">{[0, 1, 2].map((index) => <button type="button" key={index} disabled={pending} aria-current={step === index ? "step" : undefined} onClick={() => setStep(index)} className={setupButton + " text-left " + (step === index ? "border-orange-400 bg-orange-400/10 text-orange-200" : "text-slate-500")}><span className="mr-2 font-mono">{index + 1}</span>{t(`step${index + 1}`)}</button>)}</nav>}
          {optionsError && <div role="status" className="rounded-xl border border-amber-300/20 p-4 text-xs leading-5 text-amber-200">{t("optionsError")} <button type="button" disabled={pending} className="underline underline-offset-4" onClick={() => setOptionsReload((count) => count + 1)}>{t("retry")}</button></div>}
          <fieldset disabled={pending || Boolean(projectId && !project)} className="space-y-5">
            {(mode === "guided" ? [step] : [0, 1, 2]).map((index) => <section key={index} className="rounded-2xl border border-[#2a2e3a] bg-[#12141b] p-5 sm:p-6"><h2 className="mb-6 flex items-center gap-3 text-base font-semibold"><span className="flex h-7 w-7 items-center justify-center rounded-full bg-orange-400/10 font-mono text-xs text-orange-300">{index + 1}</span>{t(`step${index + 1}`)}</h2><ProjectSetupFields step={index} name={name} onName={(next) => { setName(next); setSaved(false); }} value={value} patch={patch} context={context} options={options} invalidFields={invalidFields} onReadingFile={setReadingFile} /></section>)}
          </fieldset>
          {validation && <p role="alert" className="text-sm text-rose-300">{t("validation")}</p>}
          {saved && <p role="status" className="text-sm text-emerald-300"><Check size={16} className="mr-2 inline" />{t("draftSaved")}</p>}
          <div className="flex flex-wrap justify-between gap-3 rounded-2xl border border-[#2a2e3a] bg-[#12141b] p-4">
            <button type="button" disabled={pending || !name.trim() || Boolean(projectId && !project)} className={setupButton} onClick={() => void save(true)}><Save size={16} className="mr-2 inline" />{t(saving ? "saving" : "saveDraft")}</button>
            <div className="flex gap-2">{mode === "guided" && step > 0 && <button type="button" className={setupButton} disabled={pending} onClick={() => setStep(step - 1)}>{t("previous")}</button>}
              {mode === "guided" && step < 2 ? <button key="next-step" type="button" disabled={pending} className={setupButton + " bg-orange-500 text-white"} onClick={(event) => { event.preventDefault(); setStep(step + 1); }}>{t("next")}<ArrowRight size={15} className="ml-2 inline" /></button> : <button key="create-project" type="submit" disabled={pending || Boolean(projectId && !project)} className={setupButton + " bg-[#ff6b4a] font-semibold text-white"}>{t(saving ? "saving" : "create")}</button>}
            </div>
          </div>
        </>}
        {Boolean(error) && <div role="alert" className="rounded-xl border border-rose-400/25 bg-rose-400/5 p-4 text-sm text-rose-200"><p>{t(errorKey)}</p>{invalidFields.length > 0 && <p className="mt-2">{invalidFields.map((field) => t(field)).join(" · ")}</p>}{apiError?.requestId && <p className="mt-2 font-mono text-xs">{apiError.requestId}</p>}<button type="button" className={setupButton + " mt-3"} disabled={pending} onClick={() => { if (projectId) setLoading(true); setReload((count) => count + 1); }}>{t("refresh")}</button></div>}
      </form>
      <ProjectSetupPreview name={name} value={value} />
    </div>}
  </div>;
}
