import { AuthSessionError, getTokenForSession } from "@/utils/auth";
import { useAuthStore } from "@/stores/auth-store";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowUpRight, Bell, Check, CheckCheck, ChevronLeft, ChevronRight, LoaderCircle, RefreshCw, Settings2, X } from "lucide-react";
import { createNotificationsApi, NotificationApiError } from "./api";
import type { InboxNotification, NotificationChannel, NotificationPage, NotificationPreference, NotificationTarget } from "./contracts";
import { useNotificationTranslation } from "./i18n";
import "./notifications.css";

export interface NotificationCenterProps {
  workspaceId: string;
  actorId: string;
  token: string | null;
  permissions: readonly string[];
  onNavigate: (workspaceRelativePath: string) => void;
  registerNavigationGuard?: (guard: () => boolean) => () => void;
}
const PAGE_SIZE = 20;
const channels = ["email", "sms", "push"] as const;
type PreferenceDraft = { channel: NotificationChannel; enabled: boolean; destination: string; original: NotificationPreference | undefined };
type TaskTarget = Extract<NotificationTarget, { kind: "platform-task" }>;
const securityFailure = (error: unknown) => error instanceof NotificationApiError && (
  error.status === 401 || error.status === 403 || error.code === "WORKSPACE_CONTEXT_MISMATCH"
);

/** Scope changes destroy the complete inbox, pending requests and preference draft. */
export function NotificationCenter(props: NotificationCenterProps) {
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  if (!props.token || !props.actorId || !props.workspaceId || !props.permissions.includes("workspace.view")) return null;
  return <ScopedNotificationCenter key={`${props.workspaceId}:${props.actorId}:${sessionEpoch}`} {...props} token={props.token} />;
}

function ScopedNotificationCenter({ workspaceId, actorId, token, permissions, onNavigate, registerNavigationGuard }: NotificationCenterProps & { token: string }) {
  const { t, i18n } = useNotificationTranslation();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const [initialToken] = useState(token);
  const api = useMemo(() => createNotificationsApi({ workspaceId, actorId }, getTokenForSession(sessionEpoch) ?? initialToken), [workspaceId, actorId, sessionEpoch, initialToken]);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"inbox" | "preferences">("inbox");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<NotificationPage>();
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState<unknown>();
  const [operationError, setOperationError] = useState<unknown>();
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<string>();
  const [preferences, setPreferences] = useState<NotificationPreference[]>();
  const [preferenceLoading, setPreferenceLoading] = useState(false);
  const [preferenceError, setPreferenceError] = useState<unknown>();
  const [draft, setDraft] = useState<PreferenceDraft>();
  const [target, setTarget] = useState<TaskTarget>();
  const root = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const bell = useRef<HTMLButtonElement>(null);
  const pollRequest = useRef<AbortController | null>(null);
  const preferenceRequest = useRef<AbortController | null>(null);
  const activeRequests = useRef(new Set<AbortController>());
  const closeRequest = useRef<(restoreFocus?: boolean) => void>(() => undefined);
  const panelId = useId();
  const titleId = useId();
  const dirty = draft !== undefined && (draft.enabled !== (draft.original?.enabled ?? false) || draft.destination.trim() !== (draft.original?.destination ?? ""));
  const conflicted = preferenceError instanceof NotificationApiError && preferenceError.status === 409;

  const reload = useCallback(async () => {
    pollRequest.current?.abort();
    const abort = new AbortController();
    pollRequest.current = abort;
    setLoading(true);
    try {
      const result = await api.list({ unreadOnly: open ? unreadOnly : false, offset: open ? offset : 0, limit: open ? PAGE_SIZE : 1 }, abort.signal);
      if (abort.signal.aborted) return;
      setPage(result);
      setReadError(undefined);
      if (open && offset > 0 && offset >= result.total) setOffset(Math.max(0, Math.floor((result.total - 1) / PAGE_SIZE) * PAGE_SIZE));
    } catch (error) {
      if (!abort.signal.aborted) { setReadError(error); if (securityFailure(error)) { setPage(undefined); setPreferences(undefined); setDraft(undefined); setTarget(undefined); } }
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  }, [api, open, unreadOnly, offset]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let sequence = 0;
    const run = async () => {
      if (document.hidden || stopped) return;
      const current = ++sequence;
      await reload();
      if (!stopped && !document.hidden && current === sequence) timer = setTimeout(() => { void run(); }, open ? 30_000 : 60_000);
    };
    const wake = () => {
      clearTimeout(timer);
      if (document.hidden) { ++sequence; pollRequest.current?.abort(); }
      else void run();
    };
    void run();
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", wake);
    return () => { stopped = true; clearTimeout(timer); pollRequest.current?.abort(); window.removeEventListener("focus", wake); document.removeEventListener("visibilitychange", wake); };
  }, [reload, open]);

  useEffect(() => {
    const requests = activeRequests.current;
    return () => { requests.forEach((request) => request.abort()); preferenceRequest.current?.abort(); };
  }, []);
  useEffect(() => registerNavigationGuard?.(() => !dirty && busy !== "preference"), [registerNavigationGuard, dirty, busy]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, busy]);

  const close = useCallback((restoreFocus = true) => {
    if (dirty || busy) { setNotice(t(dirty ? "unsaved" : "syncing")); return; }
    setOpen(false); setDraft(undefined); setTarget(undefined); setView("inbox"); setNotice("");
    if (restoreFocus) bell.current?.focus();
  }, [dirty, busy, t]);
  useEffect(() => { closeRequest.current = close; }, [close]);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus();
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); closeRequest.current(); } };
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) closeRequest.current(false); };
    document.addEventListener("keydown", key);
    document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("pointerdown", outside); };
  }, [open]);

  const loadPreferences = useCallback(async () => {
    preferenceRequest.current?.abort();
    const abort = new AbortController();
    preferenceRequest.current = abort;
    setPreferenceLoading(true);
    try {
      const result = await api.preferences(abort.signal);
      if (!abort.signal.aborted) { setPreferences(result); setPreferenceError(undefined); }
    } catch (error) { if (!abort.signal.aborted) { setPreferenceError(error); if (securityFailure(error)) setPreferences(undefined); } }
    finally { if (!abort.signal.aborted) setPreferenceLoading(false); }
  }, [api]);

  function errorText(error: unknown, fallback: "failed" | "actionFailed" | "preferenceFailed") {
    if (error instanceof AuthSessionError) return error.message;
    if (!(error instanceof NotificationApiError)) return t(fallback);
    if (error.status === 401) return t("unauthorized");
    if (error.code === "WORKSPACE_CONTEXT_MISMATCH") return t("scopeChanged");
    if (error.status === 403) return t("forbidden");
    if (error.status === 409) return t("conflict");
    if (error.status === 404) return t("missing");
    if (error.code === "NOTIFICATION_TARGET_UNAVAILABLE") return t("unsupported");
    return t(fallback);
  }
  function errorView(error: unknown, fallback: "failed" | "actionFailed" | "preferenceFailed") {
    return <div className="xj-notification-error" role="alert"><p>{errorText(error, fallback)}</p>{error instanceof NotificationApiError && error.requestId && <small>{t("requestId")}: {error.requestId}</small>}</div>;
  }
  function date(value: string) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? t("unknownTime") : new Intl.DateTimeFormat(i18n.resolvedLanguage ?? "zh", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
  }
  async function performRead(item: InboxNotification | null) {
    const abort = new AbortController(); activeRequests.current.add(abort);
    setBusy(item?.id ?? "all"); setOperationError(undefined); setNotice("");
    try {
      await api.read(item?.id ?? null, abort.signal);
      if (!abort.signal.aborted) { setNotice(t("readDone")); await reload(); }
    } catch (error) { if (!abort.signal.aborted) { setOperationError(error); if (securityFailure(error)) setPage(undefined); } }
    finally { activeRequests.current.delete(abort); if (!abort.signal.aborted) setBusy(undefined); }
  }
  async function openObject(item: InboxNotification) {
    const abort = new AbortController(); activeRequests.current.add(abort);
    setBusy(`target:${item.id}`); setOperationError(undefined); setNotice("");
    try {
      const result = await api.target(item.id, abort.signal);
      if (abort.signal.aborted) return;
      if (!item.readAt) await api.read(item.id, abort.signal);
      if (abort.signal.aborted) return;
      if (result.kind === "project") { onNavigate(`projects/${encodeURIComponent(result.projectId)}/control`); setOpen(false); }
      else setTarget(result);
      await reload();
    } catch (error) { if (!abort.signal.aborted) { setOperationError(error); if (securityFailure(error)) setPage(undefined); } }
    finally { activeRequests.current.delete(abort); if (!abort.signal.aborted) setBusy(undefined); }
  }
  async function savePreference() {
    if (!draft || conflicted) return;
    const destination = draft.destination.trim();
    if (draft.enabled && !destination || [...destination].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) { setNotice(t("invalidDestination")); return; }
    const abort = new AbortController(); activeRequests.current.add(abort);
    setBusy("preference"); setPreferenceError(undefined); setNotice("");
    try {
      const result = await api.savePreference(draft.channel, { enabled: draft.enabled, destination: destination || null, expectedVersion: draft.original?.version ?? null }, abort.signal);
      if (!abort.signal.aborted) { setPreferences((current) => [...(current ?? []).filter((value) => value.channel !== result.channel), result]); setDraft(undefined); setNotice(t("saved")); }
    } catch (error) { if (!abort.signal.aborted) { setPreferenceError(error); if (securityFailure(error)) { setPage(undefined); setPreferences(undefined); setDraft(undefined); setTarget(undefined); } } }
    finally { activeRequests.current.delete(abort); if (!abort.signal.aborted) setBusy(undefined); }
  }
  function switchView(next: "inbox" | "preferences") {
    if (dirty || busy) { setNotice(t("unsaved")); return; }
    setDraft(undefined); setView(next); setNotice(""); setOperationError(undefined); setTarget(undefined);
    if (next === "preferences") void loadPreferences();
    else preferenceRequest.current?.abort();
  }
  const badge = page?.unreadCount;
  const channelName = { email: "channelEmail", sms: "channelSms", push: "channelPush" } as const;
  const statuses = { queued: "statusQueued", running: "statusRunning", retrying: "statusRetrying", cancelling: "statusCancelling", succeeded: "statusSucceeded", failed: "statusFailed", cancelled: "statusCancelled", timed_out: "statusTimedOut" } as const;
  const taskStatus = (value: string) => Object.hasOwn(statuses, value) ? t(statuses[value as keyof typeof statuses]) : value;

  return <div className="xj-notification-center" ref={root}>
    <button ref={bell} type="button" className={`xj-notification-bell${readError ? " has-error" : ""}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? panelId : undefined} aria-label={readError ? t("unavailableLabel") : badge ? t("unreadLabel", { count: badge }) : t("bell")} title={t("title")} onClick={() => open ? close() : setOpen(true)}>
      <Bell size={18} aria-hidden="true" />{badge !== undefined && badge > 0 && <span className="xj-notification-badge" aria-hidden="true">{badge > 99 ? "99+" : badge}</span>}
      {Boolean(readError) && <span className="xj-notification-warning" aria-hidden="true">!</span>}
    </button>
    {open && <div id={panelId} ref={panel} className="xj-notification-panel" role="dialog" aria-modal="false" aria-labelledby={titleId} tabIndex={-1}>
      <header className="xj-notification-heading"><div><h2 id={titleId}>{t(view === "preferences" ? "preferences" : "title")}</h2><p>{badge !== undefined ? t("unreadLabel", { count: badge }) : t(readError ? "unavailableLabel" : "syncing")}</p></div><div className="xj-notification-toolbar">
        <button type="button" aria-label={t("refresh")} title={t("refresh")} disabled={Boolean(busy) || loading || preferenceLoading} onClick={() => view === "preferences" ? dirty ? setNotice(t("unsaved")) : void loadPreferences() : void reload()}><RefreshCw size={15} aria-hidden="true" /></button>
        <button type="button" aria-label={t("close")} title={t("close")} onClick={() => close()}><X size={17} aria-hidden="true" /></button>
      </div></header>
      {notice && <p className="xj-notification-notice" role="status">{notice}</p>}
      {Boolean(operationError) && errorView(operationError, "actionFailed")}
      {view === "inbox" ? <>
        <div className="xj-notification-filters"><div role="group" aria-label={t("title")}>{[false, true].map((value) => <button type="button" key={String(value)} aria-pressed={unreadOnly === value} disabled={Boolean(busy)} onClick={() => { setUnreadOnly(value); setOffset(0); setTarget(undefined); }}>{t(value ? "unread" : "all")}</button>)}</div><button type="button" className="xj-notification-text-button" disabled={!badge || Boolean(busy) || Boolean(readError)} title={t("readAllHint")} onClick={() => { void performRead(null); }}><CheckCheck size={14} aria-hidden="true" />{t("readAll")}</button></div>
        {Boolean(readError) && <>{errorView(readError, "failed")}{page && <p className="xj-notification-muted">{t("stale")}</p>}</>}
        <div className="xj-notification-scroll" aria-busy={loading}>
          {target ? <section className="xj-notification-task"><button type="button" className="xj-notification-text-button" onClick={() => setTarget(undefined)}><ArrowLeft size={14} aria-hidden="true" />{t("notificationsBack")}</button><h3>{t("taskDetails")}</h3><dl>
            <div><dt>{t("taskId")}</dt><dd>{target.task.id}</dd></div><div><dt>{t("taskType")}</dt><dd>{target.task.taskType}</dd></div><div><dt>{t("taskState")}</dt><dd>{taskStatus(target.task.status)}</dd></div><div><dt>{t("progress")}</dt><dd>{target.task.progress}%</dd></div><div><dt>{t("attempts")}</dt><dd>{target.task.attemptCount} / {target.task.maxAttempts}</dd></div><div><dt>{t("updated")}</dt><dd>{date(target.task.updatedAt)}</dd></div>{target.errorCode && <div><dt>{t("errorCode")}</dt><dd>{target.errorCode}</dd></div>}
          </dl>{target.projectId && permissions.includes("project.view") && <button type="button" className="xj-notification-primary" onClick={() => { onNavigate(`projects/${encodeURIComponent(target.projectId ?? "")}/control`); setOpen(false); }}>{t("openProject")}<ArrowUpRight size={14} aria-hidden="true" /></button>}</section> : <>
            {loading && !page && <p className="xj-notification-empty"><LoaderCircle size={20} aria-hidden="true" />{t("loading")}</p>}
            {page && page.items.length === 0 && !loading && !readError && <div className="xj-notification-empty"><Bell size={25} aria-hidden="true" /><strong>{t(unreadOnly ? "noUnread" : "empty")}</strong><p>{t(unreadOnly ? "noUnreadHint" : "emptyHint")}</p></div>}
            <ul className="xj-notification-list">{page?.items.map((item) => <li key={item.id} className={item.readAt ? "is-read" : "is-unread"}>
              <div className="xj-notification-item-meta"><span>{t(item.category === "task" ? "categoryTask" : item.category === "system" ? "categorySystem" : "categoryOther")}</span><time dateTime={item.createdAt} title={item.createdAt}>{date(item.createdAt)}</time></div>
              <h3><span className="xj-notification-read-dot" aria-label={t(item.readAt ? "readState" : "unreadState")} />{item.title}</h3><p className="xj-notification-body">{item.body}</p>
              <div className="xj-notification-item-actions">{!item.readAt && <button type="button" disabled={Boolean(busy) || Boolean(readError)} onClick={() => { void performRead(item); }}><Check size={13} aria-hidden="true" />{t("read")}</button>}{item.resourceId && (item.resourceType === "task" || item.resourceType === "project") ? <button type="button" title={t("objectHint")} disabled={Boolean(busy) || Boolean(readError)} onClick={() => { void openObject(item); }}>{t("viewObject")}<ArrowUpRight size={13} aria-hidden="true" /></button> : item.resourceId ? <span>{t("unsupported")}</span> : null}</div>
            </li>)}</ul>
          </>}
        </div>
        {!target && page && <div className="xj-notification-pagination"><span>{t("total", { count: page.total })} · {t("page", { page: Math.floor(offset / PAGE_SIZE) + 1 })}</span><div><button type="button" aria-label={t("previous")} disabled={offset === 0 || loading || Boolean(busy)} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}><ChevronLeft size={17} aria-hidden="true" /></button><button type="button" aria-label={t("next")} disabled={offset + PAGE_SIZE >= page.total || loading || Boolean(busy)} onClick={() => setOffset(offset + PAGE_SIZE)}><ChevronRight size={17} aria-hidden="true" /></button></div></div>}
        <footer className="xj-notification-footer"><button type="button" className="xj-notification-text-button" onClick={() => switchView("preferences")}><Settings2 size={14} aria-hidden="true" />{t("preferences")}</button>{loading && <small role="status">{t("syncing")}</small>}</footer>
      </> : <section className="xj-notification-preferences">
        <button type="button" className="xj-notification-text-button" onClick={() => switchView("inbox")}><ArrowLeft size={14} aria-hidden="true" />{t("back")}</button><p className="xj-notification-muted">{t("preferenceHint")}</p>
        {Boolean(preferenceError) && <>{errorView(preferenceError, "preferenceFailed")}<button type="button" className="xj-notification-text-button" disabled={Boolean(busy)} onClick={() => { setDraft(undefined); setNotice(""); void loadPreferences(); }}>{t("preferenceReload")}</button></>}
        {preferenceLoading && <p role="status">{t("preferenceLoading")}</p>}
        {preferences && channels.map((channel) => { const stored = preferences.find((value) => value.channel === channel); return <div key={channel} className="xj-notification-channel"><div><strong>{t(channelName[channel])}</strong><small>{t(stored ? stored.enabled ? "enabled" : "disabled" : "notConfigured")}</small></div><button type="button" disabled={Boolean(draft) || Boolean(busy) || Boolean(preferenceError) || preferenceLoading} onClick={() => { setDraft({ channel, enabled: stored?.enabled ?? false, destination: stored?.destination ?? "", original: stored }); setNotice(""); }}>{t("edit")}</button></div>; })}
        {draft && <form className="xj-notification-preference-form" onSubmit={(event) => { event.preventDefault(); void savePreference(); }}><h3>{t(channelName[draft.channel])}</h3><label className="xj-notification-checkbox"><input type="checkbox" checked={draft.enabled} disabled={Boolean(busy) || conflicted} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />{t("deliveryEnabled")}</label><label>{t("destination")}<input type={draft.channel === "email" ? "email" : draft.channel === "sms" ? "tel" : "text"} value={draft.destination} required={draft.enabled} maxLength={320} disabled={Boolean(busy) || conflicted} onChange={(event) => setDraft({ ...draft, destination: event.target.value })} /></label><small>{t("destinationHint")}</small><div className="xj-notification-form-actions"><button type="submit" className="xj-notification-primary" disabled={Boolean(busy) || conflicted}>{t(busy === "preference" ? "saving" : "save")}</button><button type="button" disabled={Boolean(busy)} onClick={() => { setDraft(undefined); setNotice(""); }}>{t("cancel")}</button></div></form>}
      </section>}
    </div>}
  </div>;
}
