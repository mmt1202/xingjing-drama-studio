import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Reply, X } from "lucide-react";
import { useSearch } from "wouter";

import { createReviewApi, type CreateReviewLinkInput, type ReviewComment, type ReviewLink } from "./api";
import { buttonClass, ErrorPanel, inputClass, LoadingMark, orderedComments, primaryClass, ReviewShell, ReviewStatus, ReviewTime, toTimecode } from "./components";
import { IssuedLink, LinkForm } from "./StaffReviewForms";
import { useReviewTranslation } from "./i18n";

type StaffPageId = "CR-011" | "CR-085" | "TM-002" | "TM-003";
type StaffProps = { pageId: StaffPageId; workspaceId?: string; projectId?: string; accessToken?: string };

export function StaffReviewPage(props: StaffProps) {
  return <StaffReviewWorkspace key={`${props.workspaceId}/${props.projectId}/${props.pageId}`} {...props} />;
}

function StaffReviewWorkspace({ pageId, workspaceId, projectId, accessToken }: StaffProps) {
  const { t } = useReviewTranslation();
  const initialFinalVersionId = new URLSearchParams(useSearch()).get("projectVersion") ?? undefined;
  const api = useMemo(() => createReviewApi({ accessToken }), [accessToken]);
  const isProject = pageId === "CR-011" || pageId === "CR-085";
  const [links, setLinks] = useState<ReviewLink[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [issued, setIssued] = useState<ReviewLink | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [selectedLink, setSelectedLink] = useState<ReviewLink | null>(null);
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const reads = useRef(0);
  const details = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const active = useRef(true);
  const busy = useRef(false);
  const missingContext = !workspaceId || (isProject && !projectId);

  const load = useCallback(async (pageToken?: string) => {
    if (missingContext) return;
    const generation = ++reads.current;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    setLoading(true);
    setError(null);
    try {
      const options = { query: filter, pageToken, signal: controller.signal };
      const page = isProject && projectId ? await api.projectLinks(projectId, options) : await api.workspaceLinks(workspaceId, options);
      if (!active.current || controller.signal.aborted || generation !== reads.current) return;
      setLinks((previous) => pageToken ? [...previous.filter((item) => !page.reviewLinks.some((next) => next.id === item.id)), ...page.reviewLinks] : page.reviewLinks);
      setNextToken(page.nextToken);
      setCanManage(page.canManage === true);
    } catch (reason) {
      if (active.current && !controller.signal.aborted && generation === reads.current) setError(reason);
    } finally {
      if (active.current && !controller.signal.aborted && generation === reads.current) setLoading(false);
    }
  }, [api, filter, isProject, missingContext, projectId, workspaceId]);

  useEffect(() => {
    active.current = true;
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => {
      active.current = false; window.clearTimeout(timer); readController.current?.abort();
      detailController.current?.abort(); reads.current += 1; details.current += 1;
    };
  }, [load]);

  async function mutate(operation: () => Promise<void>): Promise<boolean> {
    if (busy.current || !canManage) return false;
    busy.current = true; setPending(true); setError(null);
    try { await operation(); return true; }
    catch (reason) { if (active.current) setError(reason); return false; }
    finally { busy.current = false; if (active.current) setPending(false); }
  }

  async function create(input: CreateReviewLinkInput & { projectId?: string }): Promise<boolean> {
    return mutate(async () => {
      const link = isProject && projectId
        ? await api.createProjectLink(projectId, input)
        : await api.createWorkspaceLink(workspaceId!, { ...input, projectId: input.projectId! });
      if (!active.current) return;
      setIssued(link);
      await load();
    });
  }

  async function revoke(link: ReviewLink) {
    await mutate(async () => {
      const updated = await api.revokeWorkspaceLink(workspaceId!, link.id, link.version);
      if (!active.current) return;
      setSelectedLink((current) => current?.id === link.id ? { ...current, state: updated.state, version: updated.version } : current);
      await load();
    });
  }

  async function inspectLink(link: ReviewLink) {
    if (!workspaceId || busy.current) return;
    const generation = ++details.current;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setSelectedLink(link); setComments([]); setDetailLoading(true); setError(null);
    try {
      const detail = await api.reviewDetail(workspaceId, link.id, controller.signal);
      if (!active.current || controller.signal.aborted || generation !== details.current) return;
      setSelectedLink(detail.reviewLink); setComments(detail.comments);
    } catch (reason) {
      if (active.current && !controller.signal.aborted && generation === details.current) setError(reason);
    } finally {
      if (active.current && !controller.signal.aborted && generation === details.current) setDetailLoading(false);
    }
  }

  async function reply(comment: ReviewComment, body: string): Promise<boolean> {
    return mutate(async () => {
      const next = await api.replyToComment(workspaceId!, comment.id, body);
      if (active.current) setComments((current) => [...current.filter((item) => item.id !== next.id), next]);
    });
  }

  async function changeStatus(comment: ReviewComment, status: string) {
    await mutate(async () => {
      const next = await api.changeCommentStatus(workspaceId!, comment.id, status, comment.version);
      if (active.current) setComments((items) => items.map((item) => item.id === next.id ? next : item));
    });
  }

  function search(event: FormEvent) {
    event.preventDefault();
    if (query.trim() === filter) void load();
    else setFilter(query.trim());
  }

  return <ReviewShell eyebrow={t("staff")} title={t(isProject ? "projectLinks" : "workspaceLinks")} meta={projectId ?? workspaceId}>
    {missingContext ? <p role="alert" className="text-slate-300">{t("missingContext")}</p> : <>
      <div className={"grid gap-6 " + (canManage ? "xl:grid-cols-[22rem_minmax(0,1fr)]" : "")}>
        {canManage && <LinkForm key={`${projectId ?? "workspace"}:${initialFinalVersionId ?? ""}`} projectId={projectId} initialFinalVersionId={initialFinalVersionId} workspaceMode={!isProject} pending={pending} onCreate={create} loadDefaults={api.projectReviewDefaults} />}
        <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5">
          <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-serif text-xl">{t("createdLinks")}</h2><form className="flex gap-2" onSubmit={search}>
            <label><span className="sr-only">{t("search")}</span><input disabled={pending} value={query} onChange={(event) => setQuery(event.target.value)} className={inputClass} placeholder={t("search")} /></label>
            <button type="submit" disabled={pending} className={buttonClass}>{t("searchButton")}</button>
          </form></div>
          {loading && links.length === 0 ? <LoadingMark /> : links.length === 0 ? <p className="mt-5 text-sm text-slate-400">{t("noLinks")}</p> : <>
            <ul className="mt-4 space-y-3">{links.map((link) => <li key={link.id} className="rounded-xl border border-white/10 bg-slate-950/45 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><p className="break-all font-mono text-xs text-amber-200">{link.finalVideoVersionId}</p><p className="mt-2 text-sm"><ReviewStatus value={link.state} /> · <ReviewTime value={link.expiresAt} /></p><p className="mt-1 text-xs text-slate-400">{t("delivery")}: <ReviewStatus value={link.delivery?.status ?? "pending"} /></p></div>
                <div className="flex flex-wrap gap-2"><button type="button" disabled={pending} onClick={() => void inspectLink(link)} className={buttonClass}>{t("viewComments")}</button>{link.state === "active" && canManage && <button type="button" disabled={pending} onClick={() => void revoke(link)} className={buttonClass}><X size={14} className="mr-1 inline" />{t("revoke")}</button>}</div>
              </div>
            </li>)}</ul>
            {nextToken && <button disabled={loading || pending} type="button" onClick={() => void load(nextToken)} className={buttonClass + " mt-4"}>{t("more")}</button>}
          </>}
        </section>
      </div>
      {selectedLink && <StaffCommentPanel key={selectedLink.id} link={selectedLink} comments={comments} loading={detailLoading} pending={pending} canManage={canManage} onReply={reply}
        onRefresh={() => void inspectLink(selectedLink)} onChangeStatus={(comment, status) => void changeStatus(comment, status)} />}
      {issued && <IssuedLink link={issued} />}
      {error != null && <ErrorPanel error={error} onRetry={() => selectedLink ? void inspectLink(selectedLink) : void load()} />}
    </>}
  </ReviewShell>;
}

function StaffCommentPanel({ link, comments, loading, pending, canManage, onRefresh, onChangeStatus, onReply }: {
  link: ReviewLink; comments: ReviewComment[]; loading: boolean; pending: boolean; canManage: boolean;
  onRefresh: () => void; onChangeStatus: (comment: ReviewComment, status: string) => void;
  onReply: (comment: ReviewComment, body: string) => Promise<boolean>;
}) {
  const { t } = useReviewTranslation();
  const [replyTo, setReplyTo] = useState<ReviewComment | null>(null);
  const [body, setBody] = useState("");
  const [saved, setSaved] = useState(false);
  const transitions: Record<string, { status: string; label: string }> = {
    open: { status: "in_progress", label: "startProgress" }, in_progress: { status: "resolved", label: "resolve" },
    resolved: { status: "reopened", label: "reopen" }, reopened: { status: "resolved", label: "resolve" },
  };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!replyTo || !body.trim() || pending) return;
    if (await onReply(replyTo, body.trim())) { setReplyTo(null); setBody(""); setSaved(true); }
  }
  return <section className="mt-6 rounded-2xl border border-white/10 bg-white/[.035] p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-serif text-xl">{t("comments")}</h2><button type="button" disabled={pending || loading} onClick={onRefresh} className={buttonClass}>{t("refresh")}</button></div>
    <p className="mt-2 break-all font-mono text-xs text-amber-200">{link.finalVideoVersionId}</p>
    {loading ? <LoadingMark /> : comments.length === 0 ? <p className="mt-4 text-sm text-slate-400">{t("noComments")}</p> : <ul className="mt-4 space-y-3">{orderedComments(comments).map(({ comment, depth }) => <li key={comment.id} className={"rounded-xl border border-white/10 bg-slate-950/50 p-4 " + (depth ? "ml-5" : "")}>
      <div className="flex justify-between gap-3 text-xs"><span className="font-mono text-amber-200">{toTimecode(comment.timecodeMs)}</span><span className="text-slate-400"><ReviewStatus value={comment.status} /></span></div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm">{comment.body}</p>
      {canManage && <div className="mt-3 flex flex-wrap gap-3">
        {transitions[comment.status] && <button type="button" disabled={pending} onClick={() => onChangeStatus(comment, transitions[comment.status].status)} className={buttonClass}>{t(transitions[comment.status].label)}</button>}
        {link.state === "active" && <button type="button" disabled={pending} onClick={() => { setReplyTo(comment); setSaved(false); }} className={buttonClass}><Reply size={14} className="mr-1 inline" />{t("reply")}</button>}
      </div>}
    </li>)}</ul>}
    {replyTo && canManage && link.state === "active" && <form onSubmit={(event) => void submit(event)} className="mt-5 space-y-3"><fieldset disabled={pending} className="space-y-3">
      <p className="text-sm text-amber-100">{t("replying", { body: replyTo.body })}</p><label className="block"><span className="sr-only">{t("replyBody")}</span><textarea value={body} maxLength={10000} required onChange={(event) => setBody(event.target.value)} className={inputClass + " min-h-24"} placeholder={t("replyBody")} /></label>
      <div className="flex gap-3"><button type="submit" disabled={!body.trim()} className={primaryClass}>{t("sendReply")}</button><button type="button" onClick={() => setReplyTo(null)} className={buttonClass}>{t("cancel")}</button></div>
    </fieldset></form>}
    {saved && <p role="status" className="mt-4 text-sm text-emerald-200">{t("replySaved")}</p>}
  </section>;
}
