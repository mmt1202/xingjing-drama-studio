import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Clock3, LockKeyhole, MessageSquareText, Plus, Reply, Undo2 } from "lucide-react";

import type { FinalVideo, InternalComment, ReviewRound } from "./api";
import { irButton, irField, irPrimary, useInternalReviewTranslation } from "./i18n";

type DirtyCallback = (dirty: boolean) => void;

const panelClass = "rounded-2xl border border-[#2A2E3A] bg-[#171922] p-5";
const labelClass = "block space-y-2 text-xs font-medium text-slate-400";

function seconds(milliseconds: number): string {
  return String(Math.round(milliseconds) / 1000);
}

function FormMessage({ children, error = false }: { children: string; error?: boolean }) {
  return <p role={error ? "alert" : "status"} className={"mt-3 text-sm leading-6 " + (error ? "text-rose-300" : "text-emerald-300")}>{children}</p>;
}

export function RoundForm({ finalVideos, initialVersionId, pending, disabled = false, onCreate, onDirty, onCancel }: {
  finalVideos: FinalVideo[];
  initialVersionId?: string;
  pending: boolean;
  disabled?: boolean;
  onCreate: (input: { finalVideoVersionId: string; title: string; note: string }) => Promise<boolean>;
  onDirty: DirtyCallback;
  onCancel: () => void;
}) {
  const { t } = useInternalReviewTranslation();
  const [versionId, setVersionId] = useState(() => finalVideos.some((video) => video.id === initialVersionId) ? initialVersionId! : "");
  const [baselineVersion, setBaselineVersion] = useState(versionId);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const selected = finalVideos.find((video) => video.id === versionId);
  const dirty = Boolean(title || note || versionId !== baselineVersion);

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled || pending || busy.current) return;
    if (!selected || !title.trim()) { setError(t("inputRequired")); return; }
    busy.current = true;
    setError(null);
    try {
      if (await onCreate({ finalVideoVersionId: selected.id, title: title.trim(), note: note.trim() })) {
        setTitle(""); setNote(""); setBaselineVersion(versionId);
      }
    } catch { setError(t("actionFailed")); }
    finally { busy.current = false; }
  }

  return <form onSubmit={(event) => void submit(event)} className={panelClass + " border-[#FF6B4A]/35"}>
    <h2 className="flex items-center gap-2 text-base font-semibold text-white"><Plus size={18} className="text-[#FF6B4A]" />{t("createRound")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("roundFormHelp")}</p>
    <fieldset disabled={pending || disabled} className="mt-5 space-y-4">
      <label className={labelClass}><span>{t("finalVersion")}</span><select required value={versionId} onChange={(event) => { setVersionId(event.target.value); setError(null); }} className={irField}>
        <option value="">{t("chooseVersion")}</option>
        {versionId && !selected ? <option value={versionId} disabled>{t("versionUnavailable")}</option> : null}
        {finalVideos.map((video) => <option key={video.id} value={video.id}>{t("finalOption", { revision: video.revision, episode: video.episodeId, duration: seconds(video.durationMs) })}</option>)}
      </select></label>
      {finalVideos.length === 0 ? <p className="text-sm leading-6 text-amber-200">{t("noFinalVersions")}</p> : null}
      <label className={labelClass}><span>{t("roundTitle")}</span><input required maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("roundTitlePlaceholder")} className={irField} /></label>
      <label className={labelClass}><span>{t("roundNote")}</span><textarea maxLength={4000} value={note} onChange={(event) => setNote(event.target.value)} placeholder={t("roundNotePlaceholder")} className={irField + " min-h-24 resize-y"} /></label>
      <div className="flex flex-wrap gap-3"><button type="submit" disabled={!selected || !title.trim()} className={irPrimary}>{pending ? t("pending") : t("create")}</button><button type="button" onClick={onCancel} className={irButton}>{t("cancel")}</button></div>
    </fieldset>
    {error ? <FormMessage error>{error}</FormMessage> : null}
  </form>;
}

export function CommentForm({ round, finalVideo, replyTo, pending, disabled = false, enabled, currentTime, onSubmit, onDirty, onCancelReply }: {
  round: ReviewRound;
  finalVideo: FinalVideo;
  replyTo: InternalComment | null;
  pending: boolean;
  disabled?: boolean;
  enabled: boolean;
  currentTime: () => number;
  onSubmit: (input: { timecodeMs: number; shotId?: string; body: string; severity: string; parentCommentId?: string }) => Promise<boolean>;
  onDirty: DirtyCallback;
  onCancelReply: () => void;
}) {
  const { t } = useInternalReviewTranslation();
  const [time, setTime] = useState("0");
  const [shotId, setShotId] = useState("");
  const [body, setBody] = useState("");
  const [severity, setSeverity] = useState("note");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<"commentSaved" | "replySaved" | null>(null);
  const busy = useRef(false);
  const canWrite = enabled && !round.locked;
  const dirty = Boolean(body || severity !== "note" || (!replyTo && (time !== "0" || shotId)));
  const displayedTime = replyTo ? seconds(replyTo.timecodeMs) : time;
  const selectedShotId = replyTo ? replyTo.shotId ?? "" : shotId;

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);

  function usePosition() {
    const position = currentTime();
    if (!Number.isFinite(position) || position < 0 || position >= finalVideo.durationMs) {
      setError(t("invalidTime")); return;
    }
    const milliseconds = Math.round(position);
    setTime(seconds(milliseconds));
    setShotId(finalVideo.shots.find((shot) => milliseconds >= shot.startMs && milliseconds < shot.endMs)?.id ?? "");
    setError(null); setSaved(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canWrite || disabled || pending || busy.current || !body.trim()) return;
    const rawMilliseconds = Number(time) * 1000;
    const milliseconds = replyTo?.timecodeMs ?? Math.round(rawMilliseconds);
    if (!displayedTime.trim() || !Number.isSafeInteger(milliseconds) || milliseconds < 0 || milliseconds >= finalVideo.durationMs || (!replyTo && Math.abs(rawMilliseconds - milliseconds) > 0.0001)) {
      setError(t("invalidTime")); return;
    }
    const anchor = replyTo ? replyTo.shotId ?? undefined : shotId || undefined;
    const shot = anchor ? finalVideo.shots.find((item) => item.id === anchor && milliseconds >= item.startMs && milliseconds < item.endMs) : undefined;
    if (anchor && (!shot || milliseconds < shot.startMs || milliseconds >= shot.endMs)) {
      setError(t("invalidShotTime")); return;
    }
    busy.current = true; setError(null); setSaved(null);
    try {
      if (await onSubmit({ timecodeMs: milliseconds, shotId: anchor, body: body.trim(), severity, parentCommentId: replyTo?.id })) {
        setBody(""); setTime("0"); setShotId(""); setSeverity("note");
        setSaved(replyTo ? "replySaved" : "commentSaved");
        if (replyTo) onCancelReply();
      }
    } catch { setError(t("actionFailed")); }
    finally { busy.current = false; }
  }

  return <section className={panelClass}>
    <h2 className="flex items-center gap-2 text-base font-semibold text-white"><MessageSquareText size={18} className="text-[#FF6B4A]" />{t("commentTitle")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("commentHelp")}</p>
    {!canWrite ? <p className="mt-4 rounded-xl border border-[#2A2E3A] bg-[#0B0D14]/60 p-3 text-sm leading-6 text-slate-400">{t(round.locked ? "roundLockedComments" : "readOnlyComments")}</p> : null}
    <form onSubmit={(event) => void submit(event)} className="mt-5"><fieldset disabled={pending || disabled || !canWrite} className="space-y-4">
      {replyTo ? <div className="rounded-xl border border-[#FF6B4A]/25 bg-[#FF6B4A]/[.05] p-3 text-xs leading-6 text-slate-300"><div className="flex items-start justify-between gap-3"><p className="min-w-0 whitespace-pre-wrap break-words">{t("replying", { body: replyTo.body })}</p><button type="button" className="shrink-0 text-[#FF967F] underline underline-offset-4" onClick={onCancelReply}>{t("cancelReply")}</button></div><p className="mt-1 text-slate-500">{t("inheritedAnchor")}</p></div> : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <label className={labelClass}><span>{t("timeSeconds")}</span><input type="number" min={0} max={seconds(Math.max(0, finalVideo.durationMs - 1))} step="0.001" required readOnly={Boolean(replyTo)} value={displayedTime} onChange={(event) => { setTime(event.target.value); setError(null); setSaved(null); }} className={irField + " tabular-nums"} /></label>
        <label className={labelClass}><span>{t("severity")}</span><select value={severity} onChange={(event) => { setSeverity(event.target.value); setSaved(null); }} className={irField}>{["note", "minor", "major", "critical"].map((value) => <option key={value} value={value}>{t(value)}</option>)}</select></label>
      </div>
      {!replyTo ? <button type="button" onClick={usePosition} className={irButton + " !py-2 text-xs"}><Clock3 size={14} />{t("useCurrentTime")}</button> : null}
      <label className={labelClass}><span>{t("shot")}</span><select disabled={Boolean(replyTo) || pending || !canWrite} value={selectedShotId ?? ""} onChange={(event) => {
        setShotId(event.target.value); setError(null); setSaved(null);
        const shot = finalVideo.shots.find((item) => item.id === event.target.value);
        if (shot) setTime(seconds(shot.startMs));
      }} className={irField}><option value="">{t("noShot")}</option>{finalVideo.shots.filter((shot, index, items) => items.findIndex((item) => item.id === shot.id) === index).map((shot) => <option key={shot.clipId} value={shot.id}>{t("shotIdOption", { id: shot.id })}</option>)}</select></label>
      <label className={labelClass}><span>{t("commentBody")}</span><textarea required maxLength={4000} value={body} onChange={(event) => { setBody(event.target.value); setSaved(null); }} placeholder={t("commentPlaceholder")} className={irField + " min-h-28 resize-y"} /></label>
      <button type="submit" disabled={!body.trim()} className={irPrimary}>{replyTo ? <Reply size={16} /> : <MessageSquareText size={16} />}{pending ? t("pending") : t(replyTo ? "sendReply" : "submitComment")}</button>
    </fieldset></form>
    {dirty ? <button type="button" disabled={pending} className={irButton + " mt-3 text-xs"} onClick={() => {
      setBody(""); setTime("0"); setShotId(""); setSeverity("note"); setError(null); setSaved(null);
      if (replyTo) onCancelReply();
    }}>{t("discardDraft")}</button> : null}
    {error ? <FormMessage error>{error}</FormMessage> : saved ? <FormMessage>{t(saved)}</FormMessage> : null}
  </section>;
}

export function DecisionForm({ round, pending, disabled = false, canDecide, canLock, onDecide, onLock, onDirty }: {
  round: ReviewRound;
  pending: boolean;
  disabled?: boolean;
  canDecide: boolean;
  canLock: boolean;
  onDecide: (decision: "approved" | "changes_requested", note: string) => Promise<boolean>;
  onLock: (note: string) => Promise<boolean>;
  onDirty: DirtyCallback;
}) {
  const { t } = useInternalReviewTranslation();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<"decisionSaved" | "lockSaved" | null>(null);
  const busy = useRef(false);
  const dirty = Boolean(note);
  const decided = round.state !== "collecting";

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);

  async function decide(decision: "approved" | "changes_requested") {
    if (!canDecide || round.locked || disabled || pending || busy.current || (decision === "approved" && round.openComments > 0)) return;
    if (decision === "changes_requested" && !note.trim()) { setError(t("decisionReasonRequired")); return; }
    busy.current = true; setError(null); setSaved(null);
    try {
      if (await onDecide(decision, note.trim())) { setNote(""); setSaved("decisionSaved"); }
    } catch { setError(t("actionFailed")); }
    finally { busy.current = false; }
  }

  async function lock() {
    if (!canLock || !decided || round.locked || disabled || pending || busy.current) return;
    busy.current = true; setError(null); setSaved(null);
    try {
      if (await onLock(note.trim())) { setNote(""); setSaved("lockSaved"); }
    } catch { setError(t("actionFailed")); }
    finally { busy.current = false; }
  }

  return <section className={panelClass}>
    <h2 className="flex items-center gap-2 text-base font-semibold text-white"><Check size={18} className="text-emerald-300" />{t("decisionTitle")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("decisionHelp")}</p>
    {round.decisionNote ? <div className="mt-4 rounded-xl border border-[#2A2E3A] bg-[#0B0D14]/60 p-3"><p className="text-xs text-slate-500">{t("recordedDecision")}</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-200">{round.decisionNote}</p></div> : null}
    {round.locked ? <p className="mt-4 flex gap-2 text-sm leading-6 text-slate-400"><LockKeyhole size={16} className="mt-1 shrink-0" />{t("roundLocked")}</p> : <>
      <fieldset disabled={pending || disabled || (!canDecide && !canLock)} className="mt-5 space-y-4">
        <label className={labelClass}><span>{t("decisionNote")}</span><textarea maxLength={4000} value={note} onChange={(event) => { setNote(event.target.value); setSaved(null); setError(null); }} placeholder={t("decisionPlaceholder")} className={irField + " min-h-28 resize-y"} /></label>
        {round.openComments > 0 ? <p className="rounded-xl border border-amber-400/25 bg-amber-400/[.05] p-3 text-xs leading-6 text-amber-200">{t("openCommentsBlock", { count: round.openComments })}</p> : null}
        {canDecide ? <div className="flex flex-wrap gap-3"><button type="button" disabled={round.openComments > 0} onClick={() => void decide("approved")} className={irPrimary}><Check size={16} />{t("decideApprove")}</button><button type="button" onClick={() => void decide("changes_requested")} className={irButton + " border-rose-400/35 text-rose-300"}><Undo2 size={16} />{t("decideChanges")}</button></div> : <p className="text-xs leading-6 text-slate-500">{t("noDecisionPermission")}</p>}
        <div className="border-t border-[#2A2E3A] pt-4"><p className="text-xs leading-6 text-slate-400">{t("lockHelp")}</p>{canLock ? <><button type="button" disabled={!decided} onClick={() => void lock()} className={irButton + " mt-3 w-full"}><LockKeyhole size={15} />{t("lockRound")}</button>{!decided ? <p className="mt-2 text-xs leading-6 text-slate-500">{t("decisionBeforeLock")}</p> : null}</> : <p className="mt-2 text-xs leading-6 text-slate-500">{t("noLockPermission")}</p>}</div>
      </fieldset>
    </>}
    {dirty ? <button type="button" disabled={pending} className={irButton + " mt-3 text-xs"} onClick={() => { setNote(""); setError(null); setSaved(null); }}>{t("discardDraft")}</button> : null}
    {error ? <FormMessage error>{error}</FormMessage> : saved && (saved === "lockSaved" ? round.locked : round.state !== "collecting") ? <FormMessage>{t(saved)}</FormMessage> : null}
  </section>;
}
