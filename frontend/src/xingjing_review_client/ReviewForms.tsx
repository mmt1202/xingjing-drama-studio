import { useRef, useState, type FormEvent } from "react";
import { Check, ImagePlus, MessageSquareText } from "lucide-react";

import { ReviewApiError, type ReviewComment, type ReviewDelivery } from "./api";
import { buttonClass, ErrorPanel, inputClass, primaryClass } from "./components";
import { useReviewTranslation } from "./i18n";

export function CommentComposer({ enabled, pending, emphasized, replyTo, onCancelReply, currentTime, onSubmit }: {
  enabled: boolean; pending: boolean; emphasized: boolean; replyTo: ReviewComment | null; onCancelReply: () => void;
  currentTime: () => number;
  onSubmit: (payload: Record<string, unknown>, file?: File | null, timecodeMs?: number) => Promise<boolean>;
}) {
  const { t } = useReviewTranslation();
  const [body, setBody] = useState("");
  const [timecode, setTimecode] = useState("0");
  const [severity, setSeverity] = useState("normal");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<unknown>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = replyTo?.timecodeMs ?? Number(timecode);
    if (pending || !body.trim() || !Number.isSafeInteger(parsed) || parsed < 0) return;
    if (file && (file.size > 10 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type))) {
      setError(new ReviewApiError(400, "REVIEW_SCREENSHOT_INVALID"));
      return;
    }
    setError(null);
    const success = await onSubmit({ action: "comment", body: body.trim(), timecodeMs: parsed, severity, parentCommentId: replyTo?.id }, file, parsed);
    if (success) {
      setBody("");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return <section className={"rounded-2xl border p-5 " + (emphasized ? "border-amber-300/45 bg-amber-300/[.06]" : "border-white/10 bg-white/[.035]")}>
    <h2 className="flex items-center gap-2 font-serif text-xl"><MessageSquareText size={18} className="text-amber-300" />{t("commentTitle")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("commentHelp")}</p>
    {enabled ? <form className="mt-5" onSubmit={(event) => void submit(event)}><fieldset disabled={pending} className="space-y-3 disabled:opacity-60">
      {replyTo && <div className="flex items-center justify-between gap-3 rounded-lg border border-amber-300/20 px-3 py-2 text-xs text-amber-100"><span className="truncate">{t("replying", { body: replyTo.body })}</span><button type="button" onClick={onCancelReply}>{t("cancel")}</button></div>}
      <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
        <label className="text-xs text-slate-400">{t("timecode")}<input type="number" min={0} step={1} required value={replyTo?.timecodeMs ?? timecode} readOnly={Boolean(replyTo)} onChange={(event) => setTimecode(event.target.value)} className={inputClass + " mt-1"} /></label>
        <label className="text-xs text-slate-400">{t("severity")}<select value={severity} onChange={(event) => setSeverity(event.target.value)} className={inputClass + " mt-1"}>{["normal", "important", "blocking"].map((value) => <option key={value} value={value}>{t(value)}</option>)}</select></label>
      </div>
      {!replyTo && <button type="button" className="text-xs text-amber-200" onClick={() => setTimecode(String(currentTime()))}>{t("useCurrentTime")}</button>}
      <label className="block"><span className="sr-only">{t("commentBody")}</span><textarea value={body} maxLength={10000} required onChange={(event) => setBody(event.target.value)} className={inputClass + " min-h-28"} placeholder={t("commentBody")} /></label>
      <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300"><ImagePlus size={16} />{t("attachScreenshot")}<input ref={fileInput} className="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
      {file && <p className="text-xs text-amber-200">{t("selectedFile", { name: file.name })}</p>}
      <button type="submit" disabled={pending || !body.trim()} className={primaryClass}>{t("submitComment")}</button>
    </fieldset></form> : <p className="mt-5 text-sm text-slate-400">{t("noCommentPermission")}</p>}
    {error != null && <ErrorPanel error={error} />}
  </section>;
}

export function DecisionPanel({ enabled, pending, delivery, emphasized, onSubmit }: {
  enabled: boolean; pending: boolean; delivery: ReviewDelivery | null; emphasized: boolean;
  onSubmit: (payload: Record<string, unknown>) => Promise<boolean>;
}) {
  const { t } = useReviewTranslation();
  const [note, setNote] = useState("");
  const confirmed = delivery?.status === "confirmed";
  const decide = (decision: string) => { if (delivery) void onSubmit({ action: "decision", decision, note: note.trim() || undefined, version: delivery.version }); };
  return <section className={"rounded-2xl border p-5 " + (emphasized ? "border-amber-300/45 bg-amber-300/[.06]" : "border-white/10 bg-white/[.035]")}>
    <h2 className="flex items-center gap-2 font-serif text-xl"><Check size={18} className="text-emerald-300" />{t("approvalTitle")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("approvalHelp")}</p>
    {confirmed ? <p role="status" className="mt-5 rounded-xl border border-emerald-300/20 p-4 text-sm text-emerald-200">{t("confirmedHelp")}</p> : enabled ? <fieldset disabled={pending || !delivery} className="mt-5 disabled:opacity-60">
      <label className="block"><span className="sr-only">{t("approvalNote")}</span><textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={10000} className={inputClass + " min-h-28"} placeholder={t("approvalNote")} /></label>
      <div className="mt-3 flex flex-wrap gap-3">
        <button type="button" onClick={() => decide("approved")} className={primaryClass}>{t("approve")}</button>
        <button type="button" onClick={() => decide("changes_requested")} className={buttonClass}>{t("requestChanges")}</button>
        {delivery?.status === "approved" && <button type="button" onClick={() => void onSubmit({ action: "confirm_delivery", version: delivery.version })} className={buttonClass}>{t("confirmDelivery")}</button>}
      </div>
    </fieldset> : <p className="mt-5 text-sm text-slate-400">{t("noApprovePermission")}</p>}
  </section>;
}
