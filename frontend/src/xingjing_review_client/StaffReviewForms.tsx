import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Link2 } from "lucide-react";
import type { CreateReviewLinkInput, ReviewLink, ReviewPolicy } from "./api";
import { inputClass, primaryClass } from "./components";
import { useReviewTranslation } from "./i18n";

export function LinkForm({ projectId, initialFinalVersionId, workspaceMode, pending, onCreate, loadDefaults }: {
  projectId?: string; initialFinalVersionId?: string; workspaceMode: boolean; pending: boolean;
  onCreate: (input: CreateReviewLinkInput & { projectId?: string }) => Promise<boolean>;
  loadDefaults?: (projectId: string, signal?: AbortSignal) => Promise<ReviewPolicy>;
}) {
  const { t } = useReviewTranslation();
  const [videoVersion, setVideoVersion] = useState(initialFinalVersionId ?? "");
  const [targetProject, setTargetProject] = useState(projectId ?? "");
  const [expiresAt, setExpiresAt] = useState("");
  const [accessSecret, setAccessSecret] = useState("");
  const [watermarkText, setWatermarkText] = useState("");
  const [defaults, setDefaults] = useState<{ projectId: string; policy?: ReviewPolicy; error?: boolean }>();
  const [override, setOverride] = useState<{ projectId: string; policy: ReviewPolicy }>();
  const [retry, setRetry] = useState(0);
  const currentProject = (workspaceMode ? targetProject : projectId ?? "").trim();
  const defaultsPending = Boolean(loadDefaults && currentProject && defaults?.projectId !== currentProject);
  const defaultsError = defaults?.projectId === currentProject && defaults.error;
  const policy = override?.projectId === currentProject ? override.policy : defaults?.projectId === currentProject && defaults.policy ? defaults.policy : { comment: true, approve: true, download: false };
  useEffect(() => {
    if (!loadDefaults || !currentProject) return;
    const controller = new AbortController();
    void loadDefaults(currentProject, controller.signal).then((result) => {
      if (!controller.signal.aborted) setDefaults({ projectId: currentProject, policy: result });
    }).catch(() => {
      if (!controller.signal.aborted) setDefaults({ projectId: currentProject, error: true });
    });
    return () => controller.abort();
  }, [currentProject, loadDefaults, retry]);
  function changePolicy(key: keyof ReviewPolicy, value: boolean) {
    setOverride({ projectId: currentProject, policy: { ...policy, [key]: value } });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || defaultsPending || defaultsError || !videoVersion.trim() || (workspaceMode && !targetProject.trim())) return;
    const created = await onCreate({
      projectId: workspaceMode ? targetProject.trim() : undefined, finalVideoVersionId: videoVersion.trim(),
      expiresAt: expiresAt ? new Date(expiresAt).toISOString() : undefined, accessSecret: accessSecret.trim() || undefined,
      watermarkText: watermarkText.trim() || undefined, policy,
    });
    if (created) setAccessSecret("");
  }
  return <form onSubmit={(event) => void submit(event)} className="rounded-2xl border border-amber-300/25 bg-amber-300/[.05] p-5">
    <h2 className="flex items-center gap-2 font-serif text-xl"><Link2 size={18} className="text-amber-300" />{t("createLink")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("createHelp")}</p>
    <fieldset disabled={pending} className="mt-5 space-y-3 disabled:opacity-60">
      {workspaceMode && <label className="block text-xs text-slate-400">{t("projectId")}<input value={targetProject} onChange={(event) => setTargetProject(event.target.value)} className={inputClass + " mt-1"} required /></label>}
      <label className="block text-xs text-slate-400">{t("videoVersion")}<input value={videoVersion} onChange={(event) => setVideoVersion(event.target.value)} className={inputClass + " mt-1"} required /></label>
      <label className="block text-xs text-slate-400">{t("expires")}<input value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} className={inputClass + " mt-1"} type="datetime-local" /></label>
      <label className="block text-xs text-slate-400">{t("optionalSecret")}<input type="password" autoComplete="new-password" value={accessSecret} onChange={(event) => setAccessSecret(event.target.value)} className={inputClass + " mt-1"} /></label>
      <label className="block text-xs text-slate-400">{t("watermark")}<input value={watermarkText} onChange={(event) => setWatermarkText(event.target.value)} maxLength={255} className={inputClass + " mt-1"} /></label>
      {defaultsPending && <p role="status" className="text-xs text-slate-400">{t("loadingProjectDefaults")}</p>}
      {defaultsError && <p role="alert" className="text-xs text-rose-200">{t("projectDefaultsError")} <button type="button" className="underline" onClick={() => setRetry((value) => value + 1)}>{t("retry")}</button></p>}
      <fieldset disabled={defaultsPending || Boolean(defaultsError)} className="space-y-3">
        <Toggle label={t("allowComments")} value={policy.comment} onChange={(value) => changePolicy("comment", value)} /><Toggle label={t("allowApproval")} value={policy.approve} onChange={(value) => changePolicy("approve", value)} /><Toggle label={t("allowDownload")} value={policy.download} onChange={(value) => changePolicy("download", value)} />
      </fieldset>
      <button type="submit" disabled={defaultsPending || Boolean(defaultsError)} className={primaryClass + " w-full"}>{t("createLink")}</button>
    </fieldset>
  </form>;
}
function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5 text-sm"><span>{label}</span><input checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
}
export function IssuedLink({ link }: { link: ReviewLink }) {
  const { t } = useReviewTranslation();
  const href = link.token ? `${window.location.origin}/review/${encodeURIComponent(link.token)}/CL-003` : null;
  return <section className="mt-6 rounded-2xl border border-emerald-300/25 bg-emerald-300/[.07] p-5">
    <h2 className="flex items-center gap-2 font-semibold text-emerald-200"><KeyRound size={18} />{t("issuedTitle")}</h2>
    <p className="mt-2 text-sm text-slate-300">{t(href ? "issuedHelp" : "redactedReplay")}</p>
    {href && <p className="mt-4 break-all rounded-lg bg-slate-950/70 p-3 font-mono text-xs text-amber-100">{href}</p>}
    {link.accessSecret && <p className="mt-2 rounded-lg bg-slate-950/70 p-3 font-mono text-xs text-amber-100">{t("issuedCode", { code: link.accessSecret })}</p>}
  </section>;
}
