import { useState, type FormEvent } from "react";
import { KeyRound, Link2 } from "lucide-react";
import type { CreateReviewLinkInput, ReviewLink } from "./api";
import { inputClass, primaryClass } from "./components";
import { useReviewTranslation } from "./i18n";

export function LinkForm({ projectId, workspaceMode, pending, onCreate }: {
  projectId?: string; workspaceMode: boolean; pending: boolean;
  onCreate: (input: CreateReviewLinkInput & { projectId?: string }) => Promise<boolean>;
}) {
  const { t } = useReviewTranslation();
  const [videoVersion, setVideoVersion] = useState("");
  const [targetProject, setTargetProject] = useState(projectId ?? "");
  const [expiresAt, setExpiresAt] = useState("");
  const [accessSecret, setAccessSecret] = useState("");
  const [watermarkText, setWatermarkText] = useState("");
  const [comment, setComment] = useState(true);
  const [approve, setApprove] = useState(true);
  const [download, setDownload] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || !videoVersion.trim() || (workspaceMode && !targetProject.trim())) return;
    const created = await onCreate({
      projectId: workspaceMode ? targetProject.trim() : undefined, finalVideoVersionId: videoVersion.trim(),
      expiresAt: expiresAt ? new Date(expiresAt).toISOString() : undefined, accessSecret: accessSecret.trim() || undefined,
      watermarkText: watermarkText.trim() || undefined, policy: { comment, approve, download },
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
      <Toggle label={t("allowComments")} value={comment} onChange={setComment} /><Toggle label={t("allowApproval")} value={approve} onChange={setApprove} /><Toggle label={t("allowDownload")} value={download} onChange={setDownload} />
      <button type="submit" className={primaryClass + " w-full"}>{t("createLink")}</button>
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
