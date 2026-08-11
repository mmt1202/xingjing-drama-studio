import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Check, Clock3, Download, ImagePlus, KeyRound, Link2, LogOut, MessageSquareText, RefreshCw, Reply, X } from "lucide-react";

import { createReviewApi, type CreateReviewLinkInput, type PublicReviewContext, type ReviewApi, ReviewApiError, type ReviewComment, type ReviewDelivery, type ReviewLink, type ReviewPermission } from "./api";

type PublicPageId = "CL-001" | "CL-002" | "CL-003" | "CL-004" | "CL-005";
type StaffPageId = "CR-011" | "CR-085" | "TM-002" | "TM-003";

const permissionLabels: Record<ReviewPermission, string> = {
  "review.view": "查看审片信息",
  "review.comment": "提交批注",
  "review.approve": "作出验收决定",
  "review.download": "下载交付物",
};

function errorText(error: unknown): string {
  if (!(error instanceof ReviewApiError)) return "请求未完成，请检查网络后重试。";
  if (error.status === 401 || error.status === 403 || error.status === 404) return "该审片链接无效、已过期，或访问口令不正确。";
  if (error.status === 409) return "这份审片已被其他参与者更新，请重新加载后再处理。";
  if (error.status === 503) return "审片服务暂时不可用，请稍后重试。";
  return `操作未完成（${error.code}）。`;
}

function formatTime(value: string | null): string {
  if (!value) return "未设置期限";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function toTimecode(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}.${String(milliseconds % 1000).padStart(3, "0")}`;
}

function ErrorPanel({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return <div role="alert" className="mt-5 flex items-center justify-between gap-4 rounded-2xl border border-rose-400/30 bg-rose-950/40 px-4 py-3 text-sm text-rose-100"><span>{error}</span>{onRetry && <button type="button" onClick={onRetry} className="inline-flex items-center gap-2 rounded-lg border border-rose-300/30 px-3 py-2 text-xs font-medium"><RefreshCw size={14} />重试</button>}</div>;
}

export function PublicReviewPage({ pageId, reviewToken }: { pageId: PublicPageId; reviewToken: string }) {
  const api = useMemo(() => createReviewApi(), []);
  const [secret, setSecret] = useState("");
  const [context, setContext] = useState<PublicReviewContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async (accessSecret = secret) => {
    setLoading(true); setError(null);
    try { setContext(await api.openPublicContext(reviewToken, accessSecret || undefined)); }
    catch (reason) { setContext(null); setError(errorText(reason)); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    let active = true;
    void api.openPublicContext(reviewToken)
      .then((next) => { if (active) setContext(next); })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, reviewToken]); // Review tokens deliberately open without editor/session context.

  if (loading) return <ReviewShell eyebrow="客户审片" title="正在验证审片链接"><LoadingMark /></ReviewShell>;
  if (!context) return <ReviewShell eyebrow="访问验证" title="输入访问口令继续审片"><p className="max-w-xl text-sm leading-6 text-slate-300">受保护的审片链接需要由项目方提供访问口令。口令不会被保存在浏览器中。</p><form onSubmit={(event) => { event.preventDefault(); void load(); }} className="mt-8 flex max-w-lg gap-3"><label className="sr-only" htmlFor="review-access-secret">访问口令</label><input id="review-access-secret" value={secret} onChange={(event) => setSecret(event.target.value)} className="min-w-0 flex-1 rounded-xl border border-white/15 bg-slate-950 px-4 py-3 text-sm outline-none focus:border-amber-300" placeholder="访问口令" type="password" autoComplete="off" /><button className="rounded-xl bg-amber-300 px-5 py-3 text-sm font-bold text-slate-950" type="submit">验证</button></form>{error && <ErrorPanel error={error} />}</ReviewShell>;
  return <PublicReviewSurface pageId={pageId} token={reviewToken} api={api} context={context} refresh={() => void load(secret)} />;
}

function PublicReviewSurface({ pageId, token, api, context, refresh }: { pageId: PublicPageId; token: string; api: ReviewApi; context: PublicReviewContext; refresh: () => void }) {
  const [comments, setComments] = useState(context.comments);
  const [delivery, setDelivery] = useState<ReviewDelivery | null>(context.review.delivery);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [replyTo, setReplyTo] = useState<ReviewComment | null>(null);
  const permissions = new Set(context.session.permissions);
  const canComment = context.review.policy.comment && permissions.has("review.comment");
  const canApprove = context.review.policy.approve && permissions.has("review.approve");
  const decisionRequired = pageId === "CL-001";
  const commentRequired = pageId === "CL-002";
  const playerFocus = pageId === "CL-005";
  const mediaUrl = api.mediaUrl(token, context.session.id, context.session.ticket);

  async function action(payload: Record<string, unknown>) {
    setPending(true); setError(null);
    try {
      const result = await api.publicAction(token, context.session.id, payload);
      if (result.comment) { setComments((previous) => [...previous, result.comment as ReviewComment]); setReplyTo(null); }
      if (result.delivery) setDelivery(result.delivery);
    } catch (reason) { setError(errorText(reason)); }
    finally { setPending(false); }
  }

  async function logout() {
    setPending(true); setError(null);
    try { await api.logout(token, context.session.id); window.location.reload(); }
    catch (reason) { setError(errorText(reason)); setPending(false); }
  }

  return <ReviewShell eyebrow="星镜剧创 · 客户审片" title="交付审阅室" meta={context.review.finalVideoVersionId}>
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <section className="overflow-hidden rounded-[1.5rem] border border-white/10 bg-slate-900 shadow-2xl shadow-black/30">
        <div className="relative"><video className="aspect-video w-full bg-black" src={mediaUrl} controls preload="metadata"><track kind="captions" srcLang="zh" label="成片字幕" /></video>{context.review.watermarkText && <div className="pointer-events-none absolute inset-0 grid grid-cols-2 place-items-center overflow-hidden text-sm font-bold text-white/20 [text-shadow:0_1px_3px_rgba(0,0,0,.8)]"><span className="-rotate-12">{context.review.watermarkText}</span><span className="-rotate-12">{context.review.watermarkText}</span><span className="-rotate-12">{context.review.watermarkText}</span><span className="-rotate-12">{context.review.watermarkText}</span></div>}</div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/10 px-5 py-4 text-xs text-slate-300"><span>版本 {context.review.version} · 创建于 {formatTime(context.review.createdAt)}</span><div className="flex gap-3">{context.media.downloadAllowed && <a className="inline-flex items-center gap-1 text-amber-200" href={api.mediaUrl(token, context.session.id, context.session.ticket, true)}><Download size={14} />下载</a>}<button disabled={pending} onClick={() => void logout()} className="inline-flex items-center gap-1"><LogOut size={14} />退出</button></div></div>
      </section>
      <aside className="space-y-4"><ReviewFacts review={context.review} delivery={delivery} /><section className="rounded-2xl border border-white/10 bg-white/[0.035] p-5"><p className="text-xs font-semibold tracking-[.16em] text-amber-200">本次权限</p><ul className="mt-3 space-y-2 text-sm text-slate-200">{context.session.permissions.map((permission) => <li key={permission} className="flex gap-2"><Check size={16} className="mt-0.5 text-emerald-300" />{permissionLabels[permission]}</li>)}</ul></section></aside>
    </div>
    {error && <ErrorPanel error={error} onRetry={refresh} />}
    <div className="mt-7 grid gap-6 xl:grid-cols-2">
      <CommentComposer enabled={canComment} pending={pending} emphasized={commentRequired} replyTo={replyTo} onCancelReply={() => setReplyTo(null)} onSubmit={(payload, file, timecodeMs) => { void (async () => { const screenshotAssetId = file ? (await api.uploadScreenshot(token, context.session.id, file, timecodeMs)).id : undefined; await action({ ...payload, screenshotAssetId }); })().catch((reason: unknown) => setError(errorText(reason))); }} />
      <DecisionPanel enabled={canApprove} pending={pending} delivery={delivery} version={context.review.version} emphasized={decisionRequired} onSubmit={(payload) => void action(payload)} />
    </div>
    {!playerFocus && <CommentLedger comments={comments} token={token} api={api} context={context} onReply={setReplyTo} />}
  </ReviewShell>;
}

function ReviewFacts({ review, delivery }: { review: ReviewLink; delivery: ReviewDelivery | null }) {
  return <section className="rounded-2xl border border-white/10 bg-white/[0.035] p-5"><p className="text-xs font-semibold tracking-[.16em] text-amber-200">审片状态</p><dl className="mt-4 space-y-3 text-sm"><Fact label="到期" value={formatTime(review.expiresAt)} /><Fact label="评论" value={review.policy.comment ? "允许" : "关闭"} /><Fact label="验收" value={review.policy.approve ? "允许" : "关闭"} /><Fact label="交付" value={delivery?.status ?? "等待客户决定"} /></dl></section>;
}
function Fact({ label, value }: { label: string; value: string }) { return <div className="flex justify-between gap-4"><dt className="text-slate-400">{label}</dt><dd className="text-right text-slate-100">{value}</dd></div>; }

function CommentComposer({ enabled, pending, emphasized, replyTo, onCancelReply, onSubmit }: { enabled: boolean; pending: boolean; emphasized: boolean; replyTo: ReviewComment | null; onCancelReply: () => void; onSubmit: (payload: Record<string, unknown>, file: File | null, timecodeMs: number) => void }) {
  const [body, setBody] = useState(""); const [timecode, setTimecode] = useState("0"); const [severity, setSeverity] = useState("normal"); const [file, setFile] = useState<File | null>(null);
  function submit(event: FormEvent) { event.preventDefault(); const parsed = Number(timecode); if (!body.trim() || !Number.isInteger(parsed) || parsed < 0) return; onSubmit({ action: "comment", body: body.trim(), timecodeMs: parsed, severity, parentCommentId: replyTo?.id }, file, parsed); setBody(""); setFile(null); }
  return <section className={`rounded-2xl border p-5 ${emphasized ? "border-amber-300/45 bg-amber-300/[.06]" : "border-white/10 bg-white/[.035]"}`}><div className="flex items-center gap-2"><MessageSquareText size={18} className="text-amber-300" /><h2 className="font-serif text-xl text-white">留下批注</h2></div><p className="mt-2 text-sm leading-6 text-slate-400">批注将绑定到时间码和当前成片版本，供项目组处理。</p>{enabled ? <form className="mt-5 space-y-3" onSubmit={submit}>{replyTo && <div className="flex items-center justify-between rounded-lg border border-amber-300/20 px-3 py-2 text-xs text-amber-100"><span className="truncate">回复：{replyTo.body}</span><button type="button" onClick={onCancelReply}>取消</button></div>}<div className="grid gap-3 sm:grid-cols-[1fr_9rem]"><input value={timecode} onChange={(event) => setTimecode(event.target.value)} inputMode="numeric" className="rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" aria-label="时间码毫秒" placeholder="时间码（毫秒）" /><select value={severity} onChange={(event) => setSeverity(event.target.value)} className="rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm"><option value="normal">普通</option><option value="important">重要</option><option value="blocking">阻断</option></select></div><textarea value={body} onChange={(event) => setBody(event.target.value)} className="min-h-28 w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="请说明需要调整的内容" /><label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300"><ImagePlus size={16} />附加截图（PNG/JPEG/WebP，最大 10MB）<input className="sr-only" type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>{file && <p className="text-xs text-amber-200">已选择：{file.name}</p>}<button disabled={pending || !body.trim()} className="rounded-xl bg-amber-300 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:opacity-50">提交批注</button></form> : <AccessNotice text="此链接未授予批注权限。" />}</section>;
}

function DecisionPanel({ enabled, pending, delivery, version, emphasized, onSubmit }: { enabled: boolean; pending: boolean; delivery: ReviewDelivery | null; version: number; emphasized: boolean; onSubmit: (payload: Record<string, unknown>) => void }) {
  const [note, setNote] = useState("");
  const decide = (decision: "approved" | "changes_requested") => onSubmit({ action: "decision", decision, note: note.trim() || undefined, version });
  return <section className={`rounded-2xl border p-5 ${emphasized ? "border-amber-300/45 bg-amber-300/[.06]" : "border-white/10 bg-white/[.035]"}`}><div className="flex items-center gap-2"><Check size={18} className="text-emerald-300" /><h2 className="font-serif text-xl text-white">客户验收</h2></div><p className="mt-2 text-sm leading-6 text-slate-400">验收决定会写入服务端交付记录，不能靠前端状态伪造。</p>{enabled ? <><textarea value={note} onChange={(event) => setNote(event.target.value)} className="mt-5 min-h-28 w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="验收说明（驳回时建议写明原因）" /><div className="mt-3 flex flex-wrap gap-3"><button disabled={pending} type="button" onClick={() => decide("approved")} className="rounded-xl bg-emerald-300 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:opacity-50">确认通过</button><button disabled={pending} type="button" onClick={() => decide("changes_requested")} className="rounded-xl border border-rose-300/45 px-4 py-2.5 text-sm font-bold text-rose-200 disabled:opacity-50">退回修改</button>{delivery?.status === "approved" && <button disabled={pending} type="button" onClick={() => onSubmit({ action: "confirm_delivery", version: delivery.version })} className="rounded-xl border border-white/25 px-4 py-2.5 text-sm text-white">确认收到交付</button>}</div></> : <AccessNotice text="此链接仅允许查看，不允许作出验收决定。" />}</section>;
}

function AccessNotice({ text }: { text: string }) { return <p className="mt-5 rounded-xl border border-slate-700 bg-slate-950/70 px-3 py-3 text-sm text-slate-400">{text}</p>; }

function CommentLedger({ comments, token, api, context, onReply }: { comments: ReviewComment[]; token: string; api: ReviewApi; context: PublicReviewContext; onReply: (comment: ReviewComment) => void }) { return <section className="mt-7 rounded-2xl border border-white/10 bg-white/[.025] p-5"><div className="flex items-center justify-between"><h2 className="font-serif text-xl text-white">审片批注</h2><span className="text-sm text-slate-400">{comments.length} 条</span></div>{comments.length === 0 ? <p className="mt-5 text-sm text-slate-400">尚无批注。你可以在上方按时间码提交调整意见。</p> : <ul className="mt-5 divide-y divide-white/10">{comments.map((comment) => <li key={comment.id} className={`py-4 ${comment.parentCommentId ? "ml-8 border-l border-amber-300/20 pl-4" : ""}`}><div className="flex items-center justify-between gap-3"><span className="font-mono text-xs text-amber-200">{toTimecode(comment.timecodeMs)}</span><span className="text-xs text-slate-500">{formatTime(comment.createdAt)}</span></div><p className="mt-2 text-sm leading-6 text-slate-200">{comment.body}</p>{comment.screenshotAssetId && <img className="mt-3 max-h-64 rounded-lg border border-white/10" src={api.screenshotUrl(token, context.session.id, context.session.ticket, comment.screenshotAssetId)} alt="批注截图" />}<button type="button" onClick={() => onReply(comment)} className="mt-2 inline-flex items-center gap-1 text-xs text-amber-200"><Reply size={13} />回复</button></li>)}</ul>}</section>; }

export function StaffReviewPage({ pageId, workspaceId, projectId, accessToken }: { pageId: StaffPageId; workspaceId?: string; projectId?: string; accessToken?: string }) {
  const api = useMemo(() => createReviewApi({ accessToken }), [accessToken]);
  const isProject = pageId === "CR-011" || pageId === "CR-085";
  const [links, setLinks] = useState<ReviewLink[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [issued, setIssued] = useState<ReviewLink | null>(null);
  const [query, setQuery] = useState(""); const [nextToken, setNextToken] = useState<string | null>(null);
  const [selectedLink, setSelectedLink] = useState<ReviewLink | null>(null); const [staffComments, setStaffComments] = useState<ReviewComment[]>([]);
  const load = async (pageToken?: string) => { setLoading(true); setError(null); try { const page = isProject && projectId ? await api.projectLinks(projectId, { query, pageToken }) : workspaceId ? await api.workspaceLinks(workspaceId, { query, pageToken }) : { reviewLinks: [], nextToken: null }; setLinks((previous) => pageToken ? [...previous, ...page.reviewLinks] : page.reviewLinks); setNextToken(page.nextToken); } catch (reason) { setError(errorText(reason)); } finally { setLoading(false); } };
  useEffect(() => {
    let active = true;
    const request = isProject && projectId ? api.projectLinks(projectId) : workspaceId ? api.workspaceLinks(workspaceId) : Promise.resolve({ reviewLinks: [], nextToken: null });
    void request
      .then((next) => { if (active) { setLinks(next.reviewLinks); setNextToken(next.nextToken); } })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, isProject, projectId, workspaceId]);
  async function create(input: CreateReviewLinkInput & { projectId?: string }) { try { const link = isProject && projectId ? await api.createProjectLink(projectId, input) : workspaceId && input.projectId ? await api.createWorkspaceLink(workspaceId, { ...input, projectId: input.projectId }) : null; if (!link) return; setIssued(link); await load(); } catch (reason) { setError(errorText(reason)); } }
  async function revoke(link: ReviewLink) { if (!workspaceId) return; try { await api.revokeWorkspaceLink(workspaceId, link.id, link.version); await load(); } catch (reason) { setError(errorText(reason)); } }
  async function inspectLink(link: ReviewLink) { if (!workspaceId) return; try { const detail = await api.reviewDetail(workspaceId, link.id); setSelectedLink(detail.reviewLink); setStaffComments(detail.comments); } catch (reason) { setError(errorText(reason)); } }
  async function changeStatus(comment: ReviewComment, status: string) { if (!workspaceId) return; try { const updated = await api.changeCommentStatus(workspaceId, comment.id, status, comment.version); setStaffComments((items) => items.map((item) => item.id === updated.id ? updated : item)); } catch (reason) { setError(errorText(reason)); } }
  return <ReviewShell eyebrow="星镜剧创 · 项目协作" title={isProject ? "客户审片链接" : "审片交付管理"} meta={projectId ?? workspaceId}><div className="grid gap-6 xl:grid-cols-[22rem_minmax(0,1fr)]"><LinkForm projectId={projectId} workspaceMode={!isProject} onCreate={(input) => void create(input)} /><section className="rounded-2xl border border-white/10 bg-white/[.035] p-5"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-serif text-xl text-white">已创建链接</h2><form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void load(); }}><input value={query} onChange={(event) => setQuery(event.target.value)} className="rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-xs" placeholder="搜索项目、版本或状态" /><button className="text-sm text-amber-200">查询</button></form></div>{loading && links.length === 0 ? <LoadingMark /> : links.length === 0 ? <p className="mt-5 text-sm text-slate-400">暂无审片链接。创建链接后，链接与交付状态会在这里由服务端读取。</p> : <><ul className="mt-4 space-y-3">{links.map((link) => <li key={link.id} className="rounded-xl border border-white/10 bg-slate-950/45 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-mono text-xs text-amber-200">{link.finalVideoVersionId}</p><p className="mt-1 text-sm text-white">状态：{link.state} · 到期：{formatTime(link.expiresAt)}</p><p className="mt-1 text-xs text-slate-400">交付：{link.delivery?.status ?? "等待验收"}</p></div><div className="flex gap-2">{workspaceId && <button type="button" onClick={() => void inspectLink(link)} className="rounded-lg border border-white/20 px-3 py-2 text-xs text-amber-100">查看批注</button>}{link.state === "active" && workspaceId && <button type="button" onClick={() => void revoke(link)} className="inline-flex items-center gap-1 rounded-lg border border-rose-300/30 px-3 py-2 text-xs text-rose-200"><X size={14} />撤销</button>}</div></div></li>)}</ul>{nextToken && <button disabled={loading} type="button" onClick={() => void load(nextToken)} className="mt-4 rounded-lg border border-white/15 px-4 py-2 text-xs text-amber-200">加载更多</button>}</>}</section></div>{selectedLink && <StaffCommentPanel link={selectedLink} comments={staffComments} onChangeStatus={(comment, status) => void changeStatus(comment, status)} />}{issued && <IssuedLink link={issued} />}{error && <ErrorPanel error={error} onRetry={() => void load()} />}</ReviewShell>;
}

function StaffCommentPanel({ link, comments, onChangeStatus }: { link: ReviewLink; comments: ReviewComment[]; onChangeStatus: (comment: ReviewComment, status: string) => void }) {
  const nextStatus = (status: string) => status === "open" ? "in_progress" : status === "in_progress" ? "resolved" : status === "resolved" ? "reopened" : "resolved";
  return <section className="mt-6 rounded-2xl border border-white/10 bg-white/[.035] p-5"><div className="flex items-center justify-between"><h2 className="font-serif text-xl text-white">版本批注处理</h2><span className="font-mono text-xs text-amber-200">{link.finalVideoVersionId}</span></div>{comments.length === 0 ? <p className="mt-4 text-sm text-slate-400">当前版本暂无客户批注。</p> : <ul className="mt-4 space-y-3">{comments.map((comment) => <li key={comment.id} className="rounded-xl border border-white/10 bg-slate-950/50 p-4"><div className="flex justify-between gap-3"><span className="font-mono text-xs text-amber-200">{toTimecode(comment.timecodeMs)}</span><span className="text-xs text-slate-400">{comment.status} · v{comment.version}</span></div><p className="mt-2 text-sm text-slate-200">{comment.body}</p><button type="button" onClick={() => onChangeStatus(comment, nextStatus(comment.status))} className="mt-3 rounded-lg border border-white/20 px-3 py-2 text-xs text-amber-100">推进为 {nextStatus(comment.status)}</button></li>)}</ul>}</section>;
}

function LinkForm({ projectId, workspaceMode, onCreate }: { projectId?: string; workspaceMode: boolean; onCreate: (input: CreateReviewLinkInput & { projectId?: string }) => void }) {
  const [videoVersion, setVideoVersion] = useState(""); const [targetProject, setTargetProject] = useState(projectId ?? ""); const [expiresAt, setExpiresAt] = useState(""); const [accessSecret, setAccessSecret] = useState(""); const [watermarkText, setWatermarkText] = useState("仅供审片"); const [comment, setComment] = useState(true); const [approve, setApprove] = useState(true); const [download, setDownload] = useState(false);
  function submit(event: FormEvent) { event.preventDefault(); if (!videoVersion.trim() || (workspaceMode && !targetProject.trim())) return; onCreate({ projectId: workspaceMode ? targetProject.trim() : undefined, finalVideoVersionId: videoVersion.trim(), expiresAt: expiresAt ? new Date(expiresAt).toISOString() : undefined, accessSecret: accessSecret.trim() || undefined, watermarkText: watermarkText.trim() || undefined, policy: { comment, approve, download } }); }
  return <form onSubmit={submit} className="rounded-2xl border border-amber-300/25 bg-amber-300/[.05] p-5"><div className="flex items-center gap-2"><Link2 size={18} className="text-amber-300" /><h2 className="font-serif text-xl text-white">创建受控链接</h2></div><p className="mt-2 text-sm leading-6 text-slate-400">链接只绑定指定成片版本。访问口令仅在创建后返回一次，请立即交给客户。</p><div className="mt-5 space-y-3">{workspaceMode && <input value={targetProject} onChange={(event) => setTargetProject(event.target.value)} className="w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="项目 ID" required />}<input value={videoVersion} onChange={(event) => setVideoVersion(event.target.value)} className="w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="最终成片版本 ID" required /><label className="block text-xs text-slate-400">有效期<input value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} className="mt-1.5 w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white" type="datetime-local" /></label><input value={accessSecret} onChange={(event) => setAccessSecret(event.target.value)} className="w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="访问口令（可选）" /><input value={watermarkText} onChange={(event) => setWatermarkText(event.target.value)} maxLength={255} className="w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm" placeholder="审片水印文字（可选）" /><div className="grid gap-2 text-sm text-slate-200"><Toggle label="允许批注" value={comment} onChange={setComment} /><Toggle label="允许验收" value={approve} onChange={setApprove} /><Toggle label="允许下载" value={download} onChange={setDownload} /></div><button className="w-full rounded-xl bg-amber-300 px-4 py-2.5 text-sm font-bold text-slate-950">创建审片链接</button></div></form>;
}
function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (value: boolean) => void }) { return <label className="flex items-center justify-between rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2.5"><span>{label}</span><input checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>; }
function IssuedLink({ link }: { link: ReviewLink }) { const href = link.token ? `${window.location.origin}/review/${encodeURIComponent(link.token)}/CL-003` : null; return <section className="mt-6 rounded-2xl border border-emerald-300/25 bg-emerald-300/[.07] p-5"><div className="flex items-center gap-2 text-emerald-200"><KeyRound size={18} /><h2 className="font-semibold">仅本次展示的访问信息</h2></div><p className="mt-2 text-sm text-slate-300">请立即复制并通过安全渠道发送给客户；刷新列表不会再次显示令牌或口令。</p>{href && <p className="mt-4 break-all rounded-lg bg-slate-950/70 p-3 font-mono text-xs text-amber-100">{href}</p>}{link.accessSecret && <p className="mt-2 rounded-lg bg-slate-950/70 p-3 font-mono text-xs text-amber-100">访问口令：{link.accessSecret}</p>}</section>; }

function ReviewShell({ eyebrow, title, meta, children }: { eyebrow: string; title: string; meta?: string; children: React.ReactNode }) { return <main className="min-h-screen bg-[#080d16] px-4 py-8 text-slate-100 sm:px-7 lg:px-10"><div className="mx-auto max-w-6xl"><header className="border-b border-white/10 pb-7"><p className="text-xs font-semibold tracking-[.18em] text-amber-200">{eyebrow}</p><div className="mt-3 flex flex-wrap items-end justify-between gap-3"><h1 className="font-serif text-3xl tracking-tight text-white sm:text-4xl">{title}</h1>{meta && <span className="max-w-full truncate font-mono text-xs text-slate-400">{meta}</span>}</div></header><div className="pt-7">{children}</div></div></main>; }
function LoadingMark() { return <div role="status" className="mt-8 flex items-center gap-3 text-sm text-slate-300"><Clock3 className="animate-spin text-amber-300" size={18} />正在读取服务端审片记录…</div>; }
