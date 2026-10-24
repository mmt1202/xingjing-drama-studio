import { getTokenForSession } from "@/utils/auth";
import { useAuthStore } from "@/stores/auth-store";
import { useEffect, useMemo, useRef, useState, type FormEvent, type RefObject } from "react";
import { ArrowLeft, Check, Clock3, Film, Link2, LockKeyhole, MessageSquare, Package, Plus, RefreshCw, Reply } from "lucide-react";
import { useLocation, useSearch } from "wouter";
import { useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";
import { createInternalReviewApi, InternalReviewError, type InternalComment, type RoundDetail, type RoundPage } from "./api";
import { CommentForm, DecisionForm, RoundForm } from "./ReviewForms";
import { irButton, irField, irPrimary, useInternalReviewTranslation } from "./i18n";

const panel = "rounded-2xl border border-[#2A2E3A] bg-[#171922] p-5";
const states = ["collecting", "approved", "changes_requested", "locked"] as const;

export function timecode(milliseconds: number): string {
  const value = Math.max(0, Math.round(milliseconds));
  return `${String(Math.floor(value / 60000)).padStart(2, "0")}:${String(Math.floor(value / 1000) % 60).padStart(2, "0")}.${String(value % 1000).padStart(3, "0")}`;
}

function errorKey(error: unknown): string {
  if (!(error instanceof InternalReviewError)) return "networkError";
  if ([401, 403].includes(error.status)) return "accessError";
  if (error.status === 404) return "missingError";
  if (error.status === 409) return "conflictError";
  if (error.status === 503) return "serviceError";
  return "invalidError";
}

function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useInternalReviewTranslation();
  return <div role="alert" className="rounded-xl border border-rose-300/30 bg-rose-400/5 p-4 text-sm text-rose-100">
    <p>{t(errorKey(error))}</p>
    {error instanceof InternalReviewError && error.requestId && <p className="mt-2 text-xs text-slate-400">{t("requestId")}: {error.requestId}</p>}
    {onRetry && <button type="button" className={irButton + " mt-3"} onClick={onRetry}>{t("retry")}</button>}
  </div>;
}

export function InternalReviewPage({ workspaceId, projectId, accessToken }: { workspaceId?: string; projectId?: string; accessToken?: string }) {
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const { t } = useInternalReviewTranslation();
  if (!workspaceId || !projectId) return <main className="bg-[#0B0D14] p-8 text-slate-200"><h1>{t("title")}</h1><p role="alert" className="mt-4">{t("missingContext")}</p></main>;
  return <InternalWorkspace key={`${workspaceId}:${projectId}:${sessionEpoch}`} workspaceId={workspaceId} projectId={projectId} accessToken={accessToken} />;
}

function InternalWorkspace({ workspaceId, projectId, accessToken }: { workspaceId: string; projectId: string; accessToken?: string }) {
  const { t, i18n } = useInternalReviewTranslation();
  const [location, navigate] = useLocation();
  const search = useSearch();
  const params = new URLSearchParams(search);
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const [initialToken] = useState(accessToken);
  const api = useMemo(() => createInternalReviewApi(workspaceId, projectId, getTokenForSession(sessionEpoch) ?? initialToken), [workspaceId, projectId, sessionEpoch, initialToken]);
  const offsetValue = Number(params.get("reviewOffset") ?? 0);
  const offset = Number.isSafeInteger(offsetValue) && offsetValue >= 0 ? offsetValue : 0;
  const q = params.get("reviewQ") ?? "";
  const state = params.get("reviewState") ?? "";
  const finalVersion = params.get("projectVersion") ?? "";
  const episodeId = params.get("episodeId") ?? "";
  const [page, setPage] = useState<RoundPage>();
  const [detail, setDetail] = useState<RoundDetail>();
  const [pageError, setPageError] = useState<unknown>();
  const [detailError, setDetailError] = useState<unknown>();
  const [actionError, setActionError] = useState<unknown>();
  const [loadedKey, setLoadedKey] = useState("");
  const [detailKey, setDetailKey] = useState("");
  const [reload, setReload] = useState(0);
  const [formRevision, setFormRevision] = useState(0);
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState(false);
  const [createDirty, setCreateDirty] = useState(false);
  const [commentDirty, setCommentDirty] = useState(false);
  const [decisionDirty, setDecisionDirty] = useState(false);
  const [notice, setNotice] = useState("");
  const [replyTo, setReplyTo] = useState<InternalComment | null>(null);
  const busy = useRef(false);
  const mounted = useRef(true);
  const video = useRef<HTMLVideoElement>(null);
  const dirty = createDirty || commentDirty || decisionDirty;
  const conflict = actionError instanceof InternalReviewError && actionError.status === 409;
  const requestKey = JSON.stringify([q, state, offset, finalVersion, episodeId, reload]);
  const roundId = params.get("roundId") ?? page?.rounds[0]?.id ?? "";
  const selectionKey = `${roundId}:${reload}`;
  const pageWaiting = loadedKey !== requestKey;
  const detailWaiting = Boolean(roundId) && detailKey !== selectionKey;
  const current = detail?.round.id === roundId && (!finalVersion || detail.finalVideo.id === finalVersion) && (!episodeId || detail.finalVideo.episodeId === episodeId) ? detail : undefined;
  useProjectNavigationBlocker(dirty || pending);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void api.list({ q, state, offset, limit: 12, finalVideoVersionId: finalVersion || undefined, episodeId: episodeId || undefined }, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        if (result.workspaceId !== workspaceId || result.projectId !== projectId) throw new InternalReviewError(404, "WORKSPACE_SCOPE_MISMATCH");
        setPage(result); setPageError(undefined); setLoadedKey(requestKey);
      }).catch((error: unknown) => {
        if (!controller.signal.aborted) { setPageError(error); setLoadedKey(requestKey); }
      });
    return () => controller.abort();
  }, [api, q, state, offset, finalVersion, episodeId, reload, requestKey, workspaceId, projectId]);

  useEffect(() => {
    if (!roundId) return;
    const controller = new AbortController();
    void api.detail(roundId, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (result.workspaceId !== workspaceId || result.projectId !== projectId || result.round.id !== roundId || (finalVersion && result.finalVideo.id !== finalVersion) || (episodeId && result.finalVideo.episodeId !== episodeId)) throw new InternalReviewError(404, "WORKSPACE_SCOPE_MISMATCH");
      setDetail(result); setDetailError(undefined); setDetailKey(selectionKey);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) { setDetailError(error); setDetailKey(selectionKey); }
    });
    return () => controller.abort();
  }, [api, roundId, reload, selectionKey, workspaceId, projectId, finalVersion, episodeId]);

  useEffect(() => {
    if (!dirty && !pending) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, pending]);

  function canLeave() {
    if (!dirty && !pending) return true;
    setNotice("unsaved"); return false;
  }

  function change(values: Record<string, string>) {
    if (!canLeave()) return;
    const next = new URLSearchParams(search);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    setReplyTo(null); setActionError(undefined); setNotice("");
    navigate(`${location}?${next}`);
  }

  function go(target: string, values: Record<string, string> = {}) {
    if (!canLeave()) return;
    const query = new URLSearchParams(values);
    const final = current?.finalVideo;
    if (!query.has("episodeId") && (final?.episodeId || episodeId)) query.set("episodeId", final?.episodeId || episodeId);
    if (!query.has("timelineId") && final?.timelineId) query.set("timelineId", final.timelineId);
    if (!query.has("projectVersion") && final?.id) query.set("projectVersion", final.id);
    navigate(`/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/${target}${query.size ? `?${query}` : ""}`);
  }

  function refresh(discard = false) {
    if (!discard && !canLeave()) return;
    if (discard) {
      setCreateDirty(false); setCommentDirty(false); setDecisionDirty(false);
      setReplyTo(null); setCreating(false); setFormRevision(value => value + 1);
    }
    setActionError(undefined); setNotice(""); setReload(value => value + 1);
  }

  async function mutate(operation: () => Promise<void>): Promise<boolean> {
    if (busy.current || conflict) return false;
    busy.current = true; setPending(true); setActionError(undefined); setNotice("");
    try {
      await operation();
      if (mounted.current) { setNotice("saved"); setReload(value => value + 1); }
      return true;
    } catch (error) {
      if (mounted.current) setActionError(error);
      return false;
    } finally {
      busy.current = false;
      if (mounted.current) setPending(false);
    }
  }

  function searchRounds(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("q");
    change({ reviewQ: typeof value === "string" ? value.trim() : "", reviewOffset: "0", roundId: "" });
  }

  function seek(milliseconds: number) {
    if (!video.current || !current?.media.available) return;
    video.current.currentTime = Math.min(current.finalVideo.durationMs, Math.max(0, milliseconds)) / 1000;
    video.current.focus();
  }

  const date = (value: string | null) => value ? new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" }).format(new Date(value)) : "—";
  const denied = actionError instanceof InternalReviewError && [401, 403, 404].includes(actionError.status);
  const blocked = pending || conflict || denied || pageWaiting || detailWaiting || Boolean(pageError) || Boolean(detailError);
  if (!page) return <main className="min-h-96 bg-[#0B0D14] p-6 text-slate-200"><h1 className="text-2xl font-semibold">{t("title")}</h1><div className="mt-6">{pageError ? <ErrorNotice error={pageError} onRetry={() => refresh(true)} /> : <p role="status">{t("loading")}</p>}</div></main>;

  return <main className="min-w-0 bg-[#0B0D14] px-4 py-6 text-slate-100 sm:px-6">
    <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-xs text-violet-300">{t("eyebrow")}</p><h1 className="mt-2 text-2xl font-semibold">{t("title")}</h1><p className="mt-2 text-sm text-slate-400">{t("subtitle")}</p></div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={irButton} onClick={() => go("editing-suite")}><ArrowLeft size={15} />{t("backEditing")}</button>
        <button type="button" className={irButton} onClick={() => refresh()} disabled={pending}><RefreshCw size={15} />{t("refresh")}</button>
        {page.canManage && <button type="button" className={irPrimary} disabled={pending || pageWaiting || conflict} onClick={() => { if (canLeave()) setCreating(true); }}><Plus size={16} />{t("newRound")}</button>}
      </div>
    </header>

    {notice && <p role="status" className="mb-4 rounded-xl border border-orange-300/25 bg-orange-300/5 p-3 text-sm text-orange-100">{t(notice)}</p>}
    {pageError != null && <div className="mb-4"><ErrorNotice error={pageError} onRetry={() => refresh()} /></div>}
    {current && detailError != null && <div className="mb-4"><ErrorNotice error={detailError} onRetry={() => refresh()} /></div>}
    {dirty && Boolean(pageError || detailError || denied) && <button type="button" disabled={pending} className={irButton + " mb-4"} onClick={() => refresh(true)}>{t("discardReload")}</button>}
    {actionError != null && <div className="mb-4"><ErrorNotice error={actionError} />{conflict && <button type="button" className={irButton + " mt-3"} onClick={() => refresh(true)}>{t("discardReload")}</button>}</div>}

    <section className={panel + " mb-5"} aria-busy={pageWaiting}>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_12rem_minmax(12rem,1fr)]">
        <form className="flex min-w-0 gap-2" onSubmit={searchRounds}><input key={q} name="q" defaultValue={q} aria-label={t("searchRounds")} placeholder={t("searchRounds")} maxLength={160} className={irField} disabled={pending} /><button className={irButton}>{t("search")}</button></form>
        <select className={irField} aria-label={t("roundState")} value={state} disabled={pending} onChange={event => change({ reviewState: event.target.value, reviewOffset: "0", roundId: "" })}><option value="">{t("allStates")}</option>{states.map(value => <option key={value} value={value}>{t(value)}</option>)}</select>
        <select className={irField} aria-label={t("versionFilter")} value={finalVersion} disabled={pending} onChange={event => change({ projectVersion: event.target.value, reviewOffset: "0", roundId: "" })}><option value="">{t("allVersions")}</option>{finalVersion && !page.finalVideos.some(final => final.id === finalVersion) && <option value={finalVersion}>{t("unavailableVersion")}</option>}{page.finalVideos.map(final => <option key={final.id} value={final.id}>{final.episodeId} · {t("revision", { number: final.revision })} · {final.id}</option>)}</select>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="min-w-0 flex-1 text-xs text-slate-400">{t("selectRound")}<select className={irField + " mt-2"} value={roundId} disabled={pending || pageWaiting || page.rounds.length === 0} onChange={event => change({ roundId: event.target.value })}><option value="">{t("selectRound")}</option>{roundId && !page.rounds.some(round => round.id === roundId) && <option value={roundId}>{current?.round.title ?? roundId}</option>}{page.rounds.map(round => <option key={round.id} value={round.id}>{t("roundNumber", { number: round.number })} · {round.title} · {t(round.state)}{round.locked ? ` · ${t("locked")}` : ""}</option>)}</select></label>
        <div className="flex items-center gap-2 self-end text-xs text-slate-400"><span>{t("pagination", { total: page.total, page: Math.floor(offset / 12) + 1 })}</span><button type="button" className={irButton} disabled={pending || pageWaiting || offset === 0} onClick={() => change({ reviewOffset: String(Math.max(0, offset - 12)), roundId: "" })}>{t("previous")}</button><button type="button" className={irButton} disabled={pending || pageWaiting || offset + 12 >= page.total} onClick={() => change({ reviewOffset: String(offset + 12), roundId: "" })}>{t("next")}</button></div>
      </div>
    </section>

    {creating && <div className="mb-5"><RoundForm key={`new:${formRevision}`} finalVideos={page.finalVideos} initialVersionId={current?.finalVideo.id ?? finalVersion} pending={pending} disabled={blocked} onDirty={setCreateDirty} onCancel={() => { setCreating(false); setCreateDirty(false); }} onCreate={input => mutate(async () => {
      const result = await api.create(input);
      if (!mounted.current) return;
      setCreating(false); setCreateDirty(false); setCommentDirty(false); setDecisionDirty(false); setReplyTo(null);
      const next = new URLSearchParams(search); next.set("roundId", result.round.id); next.set("projectVersion", result.round.finalVideoVersionId); next.delete("reviewQ"); next.delete("reviewState"); next.delete("reviewOffset");
      navigate(`${location}?${next}`);
    })} /></div>}

    {pageWaiting && !current ? <p role="status" className="p-6 text-slate-400">{t("loading")}</p> : !roundId ? <section className={panel}><Film size={25} className="text-violet-300" /><h2 className="mt-4 text-lg font-semibold">{t(page.finalVideos.length ? "noRounds" : "noFinals")}</h2><p className="mt-2 text-sm text-slate-400">{t(page.finalVideos.length ? "startRoundHelp" : "renderFirst")}</p></section> : detailError && !detailWaiting && !current ? <ErrorNotice error={detailError} onRetry={() => refresh()} /> : !current ? <p role="status" className="p-6 text-slate-400">{t("loadingRound")}</p> : <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(18rem,.8fr)]">
      <div className="min-w-0 space-y-5">
        <ReviewPlayer key={current.round.id} detail={current} video={video} onRetry={() => setReload(value => value + 1)} onSeek={seek} />
        <CommentForm key={`comment:${current.round.id}:${formRevision}`} round={current.round} finalVideo={current.finalVideo} replyTo={replyTo} pending={pending} disabled={blocked} enabled={current.canComment && !current.round.locked && !denied} currentTime={() => Math.round((video.current?.currentTime ?? 0) * 1000)} onDirty={setCommentDirty} onCancelReply={() => setReplyTo(null)} onSubmit={input => mutate(async () => { await api.comment(current.round.id, { ...input, version: current.round.version }); if (mounted.current) setReplyTo(null); })} />
        <section className={panel}><div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-lg font-semibold"><MessageSquare size={18} className="text-violet-300" />{t("comments")}</h2><span className="text-xs text-slate-400">{t("commentCount", { count: current.round.totalComments })}</span></div>
          <Comments comments={current.comments} canManage={current.canManage && !current.round.locked && !blocked} onSeek={seek} onReply={comment => { if (!commentDirty) setReplyTo(comment); else setNotice("unsaved"); }} onStatus={(comment, status) => void mutate(async () => { await api.status(current.round.id, comment.id, { version: current.round.version, commentVersion: comment.version, status }); })} />
        </section>
      </div>
      <aside className="min-w-0 space-y-5">
        <section className={panel}><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">{t("roundProgress")}</h2><span className="rounded-full border border-violet-300/20 px-3 py-1 text-xs text-violet-200">{t(current.round.state)}</span></div><h3 className="mt-4 text-base font-semibold">{current.round.title}</h3>{current.round.note && <p className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-400">{current.round.note}</p>}
          <dl className="mt-5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-3 text-xs"><dt className="text-slate-400">{t("currentRound")}</dt><dd className="text-right">{t("roundNumber", { number: current.round.number })}</dd><dt className="text-slate-400">{t("openComments")}</dt><dd className="text-right">{current.round.openComments}</dd><dt className="text-slate-400">{t("configurationVersion")}</dt><dd className="text-right">{current.round.version}</dd><dt className="text-slate-400">{t("updated")}</dt><dd className="text-right">{date(current.round.updatedAt)}</dd><dt className="text-slate-400">{t("lockState")}</dt><dd className="text-right">{t(current.round.locked ? "locked" : "unlocked")}</dd></dl>
          <div className="mt-5 border-t border-[#2A2E3A] pt-4 text-xs text-slate-500"><p className="break-all">{t("versionId")}: {current.round.finalVideoVersionId}</p><p className="mt-2 break-all">{t("roundId")}: {current.round.id}</p></div>
        </section>
        <section className={panel}><h2 className="text-base font-semibold">{t("conclusion")}</h2>{current.round.decisionNote ? <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{current.round.decisionNote}</p> : <p className="mt-3 text-sm text-slate-500">{t("noConclusion")}</p>}{current.round.decidedBy && <p className="mt-3 break-words text-xs text-slate-400">{current.round.decidedBy} · {date(current.round.decidedAt)}</p>}{current.round.locked && <p className="mt-4 flex items-start gap-2 text-xs leading-5 text-violet-200"><LockKeyhole size={15} className="shrink-0" />{t("lockedHelp")}</p>}</section>
        <DecisionForm key={`decision:${current.round.id}:${formRevision}`} round={current.round} pending={pending} disabled={blocked} canDecide={current.canDecide && !current.round.locked} canLock={current.canLock && !current.round.locked} onDirty={setDecisionDirty} onDecide={(decision, note) => mutate(async () => { await api.decide(current.round.id, { version: current.round.version, decision, note }); })} onLock={note => mutate(async () => { await api.lock(current.round.id, { version: current.round.version, note }); })} />
        {!current.round.locked && current.round.state !== "collecting" && current.canManage && <button type="button" className={irButton + " w-full"} disabled={blocked || dirty} onClick={() => void mutate(async () => { await api.reopen(current.round.id, { version: current.round.version, note: "" }); })}>{t("continueCollecting")}</button>}
        <button type="button" className={irPrimary + " w-full"} disabled={blocked || dirty || current.round.state !== "approved" || !current.round.locked || !current.canHandoff} onClick={() => void mutate(async () => {
          const result = await api.handoff(current.round.id, { version: current.round.version });
          if (mounted.current) go(result.navigation.target, { projectVersion: result.navigation.projectVersion, timelineId: result.navigation.timelineId, episodeId: result.navigation.episodeId, target: result.navigation.deliveryTarget });
        })}><Package size={17} />{t("releasePackage")}</button>
        <p className="text-xs leading-5 text-slate-500">{t("handoffHelp")}</p>
        <section className={panel}><h2 className="flex items-center gap-2 text-base font-semibold"><Link2 size={17} className="text-violet-300" />{t("clientLinks")}</h2><p className="mt-3 text-xs leading-6 text-slate-400">{t("clientLinksHelp")}</p><button type="button" className={irButton + " mt-4 w-full"} disabled={pending} onClick={() => go("collaboration/CR-011")}>{t("manageClientLinks")}</button></section>
      </aside>
    </div>}
  </main>;
}

function ReviewPlayer({ detail, video, onRetry, onSeek }: { detail: RoundDetail; video: RefObject<HTMLVideoElement | null>; onRetry: () => void; onSeek: (value: number) => void }) {
  const { t } = useInternalReviewTranslation();
  const [position, setPosition] = useState(0);
  const [mediaError, setMediaError] = useState(false);
  const lastPosition = useRef(0);
  const mediaLoading = useRef(true);
  const roots = detail.comments.filter(comment => !comment.parentCommentId);
  return <section className={panel + " overflow-hidden"}><div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h2 className="flex items-center gap-2 text-lg font-semibold"><Film size={18} className="text-violet-300" />{t("player")}</h2><p className="mt-2 break-words text-xs text-slate-400">{detail.finalVideo.episodeId} · {t("revision", { number: detail.finalVideo.revision })}</p></div><span className="font-mono text-xs text-orange-200">{timecode(position)} / {timecode(detail.finalVideo.durationMs)}</span></div>
    {/* Separate caption tracks are not exposed by the immutable render contract. */}
    {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
    {detail.media.available && detail.media.url ? <div className="rounded-xl bg-black"><video ref={video} src={detail.media.url} controls controlsList="nodownload" preload="metadata" playsInline className="mx-auto max-h-[32rem] w-full rounded-xl" onEmptied={() => { mediaLoading.current = true; }} onLoadStart={() => { mediaLoading.current = true; setMediaError(false); }} onLoadedMetadata={event => { event.currentTarget.currentTime = Math.min(lastPosition.current, detail.finalVideo.durationMs) / 1000; mediaLoading.current = false; }} onTimeUpdate={event => { if (!mediaLoading.current) { lastPosition.current = Math.round(event.currentTarget.currentTime * 1000); setPosition(lastPosition.current); } }} onError={() => setMediaError(true)} aria-label={t("player")} /></div> : <div className="grid min-h-52 place-content-center gap-3 rounded-xl border border-dashed border-[#2A2E3A] p-6 text-center text-sm text-slate-400"><Film className="mx-auto text-slate-600" size={32} /><p>{t("mediaUnavailable")}</p><button type="button" className={irButton} onClick={onRetry}>{t("reloadMedia")}</button></div>}
    {mediaError && <div role="alert" className="mt-3 text-sm text-orange-200"><p>{t("mediaFailed")}</p><button type="button" className={irButton + " mt-2"} onClick={onRetry}>{t("reloadMedia")}</button></div>}
    <div className="mt-4 flex flex-wrap gap-2">{roots.map(comment => <button type="button" key={comment.id} className={irButton + " font-mono text-xs"} disabled={!detail.media.available || mediaError} onClick={() => onSeek(comment.timecodeMs)}><Clock3 size={12} />{timecode(comment.timecodeMs)}{comment.status === "resolved" && <Check size={12} />}</button>)}</div>
    <p className="mt-4 break-all font-mono text-[10px] leading-5 text-slate-500">{detail.finalVideo.id} · {detail.finalVideo.timelineVersionId}</p>
  </section>;
}

function Comments({ comments, canManage, onSeek, onReply, onStatus }: { comments: InternalComment[]; canManage: boolean; onSeek: (value: number) => void; onReply: (comment: InternalComment) => void; onStatus: (comment: InternalComment, status: string) => void }) {
  const { t } = useInternalReviewTranslation();
  const ordered = useMemo(() => {
    const result: { comment: InternalComment; depth: number }[] = [];
    const seen = new Set<string>();
    function append(comment: InternalComment, depth: number) {
      if (seen.has(comment.id)) return;
      seen.add(comment.id); result.push({ comment, depth });
      comments.filter(child => child.parentCommentId === comment.id).forEach(child => append(child, depth + 1));
    }
    comments.filter(comment => !comment.parentCommentId).forEach(comment => append(comment, 0));
    comments.forEach(comment => append(comment, 0));
    return result;
  }, [comments]);
  if (!comments.length) return <p className="mt-5 text-sm text-slate-500">{t("noComments")}</p>;
  return <ul className="mt-5 space-y-3">{ordered.map(({ comment, depth }) => <li key={comment.id} className={`rounded-xl border p-4 ${depth ? "ml-4 border-[#2A2E3A] bg-[#0B0D14]/40" : comment.status === "resolved" ? "border-emerald-300/20 bg-emerald-300/[.025]" : "border-orange-300/25 bg-[#0B0D14]/60"}`}>
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex min-w-0 flex-wrap items-center gap-2 text-xs"><button type="button" className="rounded-md border border-[#2A2E3A] px-2 py-1 font-mono text-violet-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-300" onClick={() => onSeek(comment.timecodeMs)}>{timecode(comment.timecodeMs)}</button>{comment.shotId && <span className="break-all text-slate-400">{t("shot")}: {comment.shotId}</span>}<span className="break-all text-slate-500">{comment.authorId}</span></div><span className="text-xs text-orange-200">{t(comment.severity)}</span></div>
    <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-slate-200">{comment.body}</p>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-slate-400">{t(comment.parentCommentId ? "replyRecord" : comment.status)}</span>{canManage && <div className="flex flex-wrap items-center gap-2">{!comment.parentCommentId && <button type="button" className={irButton + " text-xs"} onClick={() => onReply(comment)}><Reply size={13} />{t("reply")}</button>}{!comment.parentCommentId && <select className={irField + " w-auto text-xs"} aria-label={t("issueStatus")} value={comment.status} onChange={event => onStatus(comment, event.target.value)}>{[comment.status, ...({ open: ["in_progress", "resolved"], in_progress: ["resolved", "open"], resolved: ["reopened"], reopened: ["in_progress", "resolved"] }[comment.status])].map(value => <option key={value} value={value}>{t(value)}</option>)}</select>}</div>}</div>
  </li>)}</ul>;
}
