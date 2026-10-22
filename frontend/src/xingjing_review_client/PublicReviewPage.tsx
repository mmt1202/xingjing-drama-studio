import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Check, Download, LogOut, RefreshCw, Reply } from "lucide-react";

import { createReviewApi, ReviewApiError, type PublicReviewContext, type ReviewApi, type ReviewComment } from "./api";
import { ErrorPanel, inputClass, LoadingMark, orderedComments, primaryClass, ReviewShell, ReviewStatus, ReviewTime, toTimecode } from "./components";
import { CommentComposer, DecisionPanel } from "./ReviewForms";
import { useReviewTranslation } from "./i18n";

type PublicPageId = "CL-001" | "CL-002" | "CL-003" | "CL-004" | "CL-005";

export function PublicReviewPage(props: { pageId: PublicPageId; reviewToken: string }) {
  return <PublicReviewEntry key={props.reviewToken} {...props} />;
}

function PublicReviewEntry({ pageId, reviewToken }: { pageId: PublicPageId; reviewToken: string }) {
  const { t } = useReviewTranslation();
  const api = useMemo(() => createReviewApi(), []);
  const [secret, setSecret] = useState("");
  const [context, setContext] = useState<PublicReviewContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [exited, setExited] = useState(false);
  const sequence = useRef(0);
  const pendingRead = useRef<AbortController | null>(null);
  const sessionId = useRef<string | undefined>(undefined);

  const load = useCallback(async (accessSecret?: string) => {
    const requestId = ++sequence.current;
    pendingRead.current?.abort();
    const controller = new AbortController();
    pendingRead.current = controller;
    setLoading(true);
    setError(null);
    try {
      const next = await api.openPublicContext(reviewToken, accessSecret, { sessionId: sessionId.current, signal: controller.signal });
      if (controller.signal.aborted || requestId !== sequence.current) return;
      if ("verificationRequired" in next) {
        sessionId.current = undefined;
        setContext(null);
      } else {
        sessionId.current = next.session.id;
        setContext(next);
        setSecret("");
        setExited(false);
      }
    } catch (reason) {
      if (controller.signal.aborted || requestId !== sequence.current) return;
      if (reason instanceof ReviewApiError && [401, 403, 404].includes(reason.status)) {
        sessionId.current = undefined;
        setContext(null);
      }
      setError(reason);
    } finally {
      if (!controller.signal.aborted && requestId === sequence.current) setLoading(false);
    }
  }, [api, reviewToken]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => { window.clearTimeout(timer); pendingRead.current?.abort(); sequence.current += 1; };
  }, [load]);

  const onExit = () => {
    sequence.current += 1;
    pendingRead.current?.abort();
    sessionId.current = undefined;
    setContext(null);
    setSecret("");
    setError(null);
    setLoading(false);
    setExited(true);
  };

  if (exited) return <ReviewShell eyebrow={t("client")} title={t("loggedOut")}><p className="text-slate-300">{t("loggedOutHelp")}</p><button type="button" disabled={loading} className={primaryClass + " mt-6"} onClick={() => { setExited(false); void load(); }}>{t("reenter")}</button></ReviewShell>;
  if (loading && !context) return <ReviewShell eyebrow={t("client")} title={t("verifying")}><LoadingMark /></ReviewShell>;
  if (!context) {
    const verify = (event: FormEvent) => { event.preventDefault(); void load(secret || undefined); };
    return <ReviewShell eyebrow={t("client")} title={t("accessTitle")}>
      <p className="max-w-xl text-sm leading-6 text-slate-300">{t("accessHelp")}</p>
      <form onSubmit={verify} className="mt-8 flex max-w-lg gap-3">
        <label className="sr-only" htmlFor="review-access-secret">{t("accessCode")}</label>
        <input id="review-access-secret" value={secret} onChange={(event) => setSecret(event.target.value)} className={inputClass} placeholder={t("accessCode")} type="password" autoComplete="off" />
        <button className={primaryClass} type="submit" disabled={loading}>{t("verify")}</button>
      </form>
      {error != null && <ErrorPanel error={error} />}
    </ReviewShell>;
  }
  return <>
    <PublicReviewSurface pageId={pageId} token={reviewToken} api={api} context={context} refreshing={loading}
      refresh={() => void load()} onContext={setContext} onExit={onExit} />
    {error != null && <div className="bg-[#0b1020] px-5 pb-10"><div className="mx-auto max-w-7xl"><ErrorPanel error={error} onRetry={() => void load()} /></div></div>}
  </>;
}

function PublicReviewSurface({ pageId, token, api, context, refreshing, refresh, onContext, onExit }: {
  pageId: PublicPageId; token: string; api: ReviewApi; context: PublicReviewContext; refreshing: boolean;
  refresh: () => void; onContext: (context: PublicReviewContext) => void; onExit: () => void;
}) {
  const { t } = useReviewTranslation();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const [replyTo, setReplyTo] = useState<ReviewComment | null>(null);
  const busy = useRef(false);
  const active = useRef(true);
  const video = useRef<HTMLVideoElement | null>(null);
  const playbackTime = useRef(0);
  const uploaded = useRef<{ file: File; timecode: number; id: string } | null>(null);
  const permissions = new Set(context.session.permissions);
  const canComment = context.review.policy.comment && permissions.has("review.comment");
  const canApprove = context.review.policy.approve && permissions.has("review.approve");
  const delivery = context.review.delivery;

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  async function submit(payload: Record<string, unknown>, file?: File | null, timecode = 0): Promise<boolean> {
    if (busy.current || refreshing) return false;
    busy.current = true;
    setPending(true);
    setError(null);
    try {
      let screenshotAssetId: string | undefined;
      if (file) {
        if (uploaded.current?.file !== file || uploaded.current.timecode !== timecode) {
          const screenshot = await api.uploadScreenshot(token, context.session.id, file, timecode);
          uploaded.current = { file, timecode, id: screenshot.id };
        }
        screenshotAssetId = uploaded.current.id;
      }
      const result = await api.publicAction(token, context.session.id, { ...payload, ...(screenshotAssetId ? { screenshotAssetId } : {}) });
      if (!active.current) return false;
      onContext({
        ...context,
        review: result.delivery ? { ...context.review, delivery: result.delivery } : context.review,
        comments: result.comment ? [...context.comments.filter((item) => item.id !== result.comment?.id), result.comment] : context.comments,
      });
      if (result.comment) { setReplyTo(null); uploaded.current = null; }
      return true;
    } catch (reason) {
      if (active.current) setError(reason);
      return false;
    } finally {
      busy.current = false;
      if (active.current) setPending(false);
    }
  }

  async function logout() {
    if (busy.current || refreshing) return;
    busy.current = true;
    setPending(true);
    setError(null);
    try { await api.logout(token, context.session.id); if (active.current) onExit(); }
    catch (reason) { if (active.current) setError(reason); }
    finally { busy.current = false; if (active.current) setPending(false); }
  }

  const permissionLabels = { "review.view": "permissionView", "review.comment": "permissionComment", "review.approve": "permissionApprove", "review.download": "permissionDownload" };
  return <ReviewShell eyebrow={t("client")} title={t("reviewRoom")} meta={context.review.finalVideoVersionId}>
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <section className="overflow-hidden rounded-3xl border border-white/10 bg-slate-900 shadow-2xl shadow-black/30">
        <div className="relative">
          <video ref={video} className="aspect-video w-full bg-black" src={api.mediaUrl(token, context.session.id, context.session.ticket)} controls preload="metadata"
            onTimeUpdate={(event) => { playbackTime.current = event.currentTarget.currentTime; }}
            onLoadedMetadata={(event) => { if (playbackTime.current > 0) event.currentTarget.currentTime = playbackTime.current; }}
            onError={() => setError(new ReviewApiError(503, "REVIEW_MEDIA_NOT_AVAILABLE"))}>
            <track kind="captions" />
          </video>
          {context.review.watermarkText && <div className="pointer-events-none absolute inset-0 grid grid-cols-2 place-items-center text-sm font-bold text-white/25">{[0, 1, 2, 3].map((key) => <span key={key} className="-rotate-12">{context.review.watermarkText}</span>)}</div>}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-5 py-4 text-xs text-slate-300">
          <ReviewTime value={context.review.createdAt} />
          <div className="flex flex-wrap gap-3">
            {context.media.downloadAllowed && permissions.has("review.download") && <a className="inline-flex items-center gap-1 text-amber-200" href={api.mediaUrl(token, context.session.id, context.session.ticket, true)}><Download size={14} />{t("download")}</a>}
            <button type="button" disabled={pending || refreshing} onClick={() => { setError(null); refresh(); }} className="inline-flex items-center gap-1 disabled:opacity-50"><RefreshCw size={14} />{t("refresh")}</button>
            <button type="button" disabled={pending || refreshing} onClick={() => void logout()} className="inline-flex items-center gap-1 disabled:opacity-50"><LogOut size={14} />{t("logout")}</button>
          </div>
        </div>
      </section>
      <aside className="space-y-4">
        <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5"><h2 className="text-amber-200">{t("reviewStatus")}</h2><dl className="mt-4 space-y-3 text-sm">
          <div className="flex justify-between gap-3"><dt>{t("expires")}</dt><dd><ReviewTime value={context.review.expiresAt} /></dd></div>
          <div className="flex justify-between gap-3"><dt>{t("delivery")}</dt><dd><ReviewStatus value={delivery?.status ?? "pending"} /></dd></div>
        </dl></section>
        <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5"><h2 className="text-amber-200">{t("permissions")}</h2><ul className="mt-3 space-y-2 text-sm">{context.session.permissions.map((permission) => <li key={permission} className="flex gap-2"><Check size={16} className="text-emerald-300" />{t(permissionLabels[permission])}</li>)}</ul></section>
      </aside>
    </div>
    {error != null && <ErrorPanel error={error} onRetry={refresh} />}
    <div className="mt-7 grid gap-6 xl:grid-cols-2">
      <CommentComposer enabled={canComment} pending={pending || refreshing} emphasized={pageId === "CL-002"} replyTo={replyTo} onCancelReply={() => setReplyTo(null)}
        currentTime={() => Math.round((video.current?.currentTime ?? 0) * 1000)} onSubmit={submit} />
      <DecisionPanel enabled={canApprove} pending={pending || refreshing} delivery={delivery} emphasized={pageId === "CL-001"} onSubmit={submit} />
    </div>
    {pageId !== "CL-005" && <section className="mt-7 rounded-2xl border border-white/10 bg-white/[.025] p-5">
      <div className="flex items-center justify-between"><h2 className="font-serif text-xl">{t("comments")}</h2><span className="text-sm text-slate-400">{t("commentCount", { count: context.comments.length })}</span></div>
      {context.comments.length === 0 ? <p className="mt-5 text-sm text-slate-400">{t("noComments")}</p> : <ul className="mt-5 divide-y divide-white/10">
        {orderedComments(context.comments).map(({ comment, depth }) => <li key={comment.id} className={"py-4 " + (depth ? "ml-5 border-l border-amber-300/20 pl-4" : "")}>
          <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-400">
            <button type="button" className="font-mono text-amber-200" onClick={() => { if (video.current) video.current.currentTime = comment.timecodeMs / 1000; }}>{toTimecode(comment.timecodeMs)}</button>
            <span><ReviewStatus value={comment.status} /> · <ReviewTime value={comment.createdAt} /></span>
          </div>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{comment.body}</p>
          {comment.screenshotAssetId && <img className="mt-3 max-h-64 rounded-lg border border-white/10" src={api.screenshotUrl(token, context.session.id, context.session.ticket, comment.screenshotAssetId)} alt={t("screenshot")} />}
          {canComment && <button type="button" disabled={pending || refreshing} onClick={() => setReplyTo(comment)} className="mt-2 inline-flex items-center gap-1 text-xs text-amber-200 disabled:opacity-50"><Reply size={13} />{t("reply")}</button>}
        </li>)}
      </ul>}
    </section>}
  </ReviewShell>;
}
