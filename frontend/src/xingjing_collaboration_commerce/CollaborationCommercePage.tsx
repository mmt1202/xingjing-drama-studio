import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Download,
  Handshake,
  MessageSquareText,
  PanelsTopLeft,
  ReceiptText,
  RefreshCw,
  Search,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { collaborationCommerceNamespace, ensureCollaborationCommerceI18n } from "./i18n";
import { collaborationCommerceRouteMap, retryFailedAction } from "./routes";
import type {
  CollaborationCommerceApi,
  CollaborationCommerceRouteDefinition,
  CommerceActionDefinition,
  CommerceActionFailure,
  CommercePageContext,
  CommercePageSnapshot,
  CommerceRecord,
  SessionContext,
} from "./types";
import type { CollaborationCommercePageId } from "./routes";

import "./collaboration-commerce.css";

ensureCollaborationCommerceI18n();

type Phase = "checking" | "ready" | "denied" | "failed";
type Notice =
  | { readonly kind: "pending"; readonly actionLabel: string }
  | { readonly kind: "success"; readonly requestId?: string; readonly object?: CommerceRecord }
  | { readonly kind: "processing"; readonly requestId?: string }
  | { readonly kind: "conflict"; readonly requestId?: string }
  | { readonly kind: "partial"; readonly requestId?: string; readonly failures: readonly CommerceActionFailure[]; readonly retryableIds: readonly string[]; readonly sourceAction: CommerceActionDefinition }
  | { readonly kind: "failure"; readonly requestId?: string };

interface ErrorInfo {
  readonly status: number | null;
  readonly requestId: string | null;
}

export interface CollaborationCommercePageProps {
  readonly pageId: CollaborationCommercePageId;
  readonly api: CollaborationCommerceApi;
  /** M14 必须注入此适配器；页面不会回落到旧的通用 actions 协议。 */
  readonly commercialM14Api?: CollaborationCommerceApi;
  readonly context: CommercePageContext;
}

const moduleIcons = {
  M01: ShieldCheck,
  M10: Users,
  M11: ReceiptText,
  M12: MessageSquareText,
  M13: PanelsTopLeft,
  M14: Handshake,
  M16: ReceiptText,
} as const;

function errorInfo(cause: unknown): ErrorInfo {
  if (typeof cause !== "object" || cause === null) return { status: null, requestId: null };
  const value = cause as { readonly status?: unknown; readonly requestId?: unknown };
  return {
    status: typeof value.status === "number" ? value.status : null,
    requestId: typeof value.requestId === "string" ? value.requestId : null,
  };
}

function hasPermission(session: SessionContext, permission: string): boolean {
  return session.permissions.includes(permission) || session.permissions.includes("*");
}

function safeMediaUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value, window.location.href);
    if (parsed.protocol === "https:" || (parsed.protocol === "http:" && parsed.origin === window.location.origin)) return parsed.href;
  } catch {
    return null;
  }
  return null;
}

function formatValue(value: unknown, format: string | undefined, language: string, currency = "CNY"): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(String).join("、") || "—";
  if (format === "minor" && typeof value === "number") {
    return new Intl.NumberFormat(language, { style: "currency", currency }).format(value / 100);
  }
  if (format === "integer" && typeof value === "number") return new Intl.NumberFormat(language).format(value);
  if ((format === "date" || format === "datetime") && typeof value === "string") {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return new Intl.DateTimeFormat(language, format === "date" ? { dateStyle: "medium" } : { dateStyle: "medium", timeStyle: "short" }).format(date);
  }
  if (format === "version") return typeof value === "number" ? `v${value}` : typeof value === "string" ? value : "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "—";
}

function textFieldValue(values: Readonly<Record<string, string | boolean>>, name: string): string {
  const value = values[name];
  return typeof value === "string" ? value : "";
}

function ActionDialog({ action, context, onCancel, onConfirm }: {
  readonly action: CommerceActionDefinition;
  readonly context: CommercePageContext;
  readonly onCancel: () => void;
  readonly onConfirm: (payload: Readonly<Record<string, unknown>>) => void;
}) {
  const { t } = useTranslation(collaborationCommerceNamespace);
  const [values, setValues] = useState<Record<string, string | boolean>>({
    active: true,
    ...(context.projectId ? { projectId: context.projectId } : {}),
  });
  const actionLabel = t(action.labelKey);
  const warningKey = action.confirmation === "financial" ? "common.financialWarning"
    : action.confirmation === "danger" ? "common.dangerWarning"
      : action.confirmation === "approval" ? "common.approvalWarning" : "common.standardWarning";
  const valid = action.fields.every((entry) => {
    if (!entry.required) return true;
    const value = values[entry.name];
    return typeof value === "boolean" ? value : Boolean(value?.trim());
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!valid) return;
    const payload = Object.fromEntries(action.fields.map((entry) => {
      const raw = values[entry.name] ?? (entry.type === "checkbox" ? false : "");
      return [entry.name, entry.type === "number" && raw !== "" ? Number(raw) : raw];
    }));
    onConfirm(payload);
  };

  return (
    <div className="xj-cc-dialog-backdrop">
      <section role={action.confirmation === "none" ? "dialog" : "alertdialog"} aria-modal="true" aria-label={t("common.confirmAction", { action: actionLabel })} className="xj-cc-dialog">
        <div className="xj-cc-dialog-head">
          <div><span className="xj-cc-eyebrow">{t("common.actions")}</span><h2>{t("common.confirmAction", { action: actionLabel })}</h2></div>
          <button type="button" className="xj-cc-icon-button" aria-label={t("common.cancel")} onClick={onCancel}><X aria-hidden="true" size={18} /></button>
        </div>
        <p className={`xj-cc-confirmation is-${action.confirmation}`}><ShieldCheck aria-hidden="true" size={18} />{t(warningKey)}</p>
        <form onSubmit={submit}>
          {action.fields.map((entry) => (
            <label key={entry.name} className="xj-cc-field">
              <span>{t(entry.labelKey)}</span>
              {entry.type === "textarea" ? (
                <textarea required={entry.required} value={textFieldValue(values, entry.name)} onChange={(event) => setValues((current) => ({ ...current, [entry.name]: event.target.value }))} />
              ) : entry.type === "checkbox" ? (
                <input type="checkbox" checked={values[entry.name] === true} onChange={(event) => setValues((current) => ({ ...current, [entry.name]: event.target.checked }))} />
              ) : (
                <input type={entry.type} min={entry.min} required={entry.required} value={textFieldValue(values, entry.name)} onChange={(event) => setValues((current) => ({ ...current, [entry.name]: event.target.value }))} />
              )}
            </label>
          ))}
          <div className="xj-cc-dialog-actions">
            <button type="button" className="xj-cc-button is-quiet" onClick={onCancel}>{t("common.cancel")}</button>
            <button type="submit" className="xj-cc-button is-primary" disabled={!valid}>{t("common.confirmAction", { action: actionLabel })}</button>
          </div>
        </form>
      </section>
    </div>
  );
}

function StatePanel({ route, phase, requestId, onRetry }: {
  readonly route: CollaborationCommerceRouteDefinition;
  readonly phase: Phase;
  readonly requestId: string | null;
  readonly onRetry: () => void;
}) {
  const { t } = useTranslation(collaborationCommerceNamespace);
  if (phase === "checking") return <section className="xj-cc-state" role="status" aria-busy="true"><span className="xj-cc-loader" /><h2>{t("common.checking")}</h2></section>;
  const denied = phase === "denied";
  return (
    <section className={`xj-cc-state is-${phase}`} role="alert">
      {denied ? <ShieldCheck aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
      <h2>{t(denied ? `common.denied.${route.module}` : `common.unavailable.${route.module}`)}</h2>
      <p>{denied ? t("common.deniedHelp") : t("common.standardWarning")}</p>
      {requestId && <small>{t("common.requestId", { id: requestId })}</small>}
      {!denied && <button type="button" className="xj-cc-button is-primary" onClick={onRetry}><RefreshCw aria-hidden="true" size={16} />{t("common.retry")}</button>}
    </section>
  );
}

function NoticePanel({ notice, onRefresh, onRetryFailed }: {
  readonly notice: Notice;
  readonly onRefresh: () => void;
  readonly onRetryFailed: (notice: Extract<Notice, { kind: "partial" }>) => void;
}) {
  const { t } = useTranslation(collaborationCommerceNamespace);
  if (notice.kind === "pending") return <div className="xj-cc-notice is-pending" role="status"><Clock3 aria-hidden="true" size={18} /><span>{t("common.pendingAction", { action: notice.actionLabel })}</span></div>;
  if (notice.kind === "conflict") return <div className="xj-cc-notice is-conflict" role="alert"><AlertTriangle aria-hidden="true" size={18} /><div><strong>{t("common.conflict")}</strong><p>{t("common.conflictHelp")}</p>{notice.requestId && <small>{t("common.requestId", { id: notice.requestId })}</small>}</div><button type="button" className="xj-cc-button" onClick={onRefresh}>{t("common.refresh")}</button></div>;
  if (notice.kind === "partial") return <div className="xj-cc-notice is-partial" role="alert"><AlertTriangle aria-hidden="true" size={18} /><div><strong>{t("common.partial")}</strong>{notice.failures.map((failure) => <p key={`${failure.id}-${failure.code}`}><code>{failure.id}</code><span>{failure.message}</span></p>)}{notice.requestId && <small>{t("common.requestId", { id: notice.requestId })}</small>}</div>{notice.retryableIds.length > 0 && <button type="button" className="xj-cc-button" onClick={() => onRetryFailed(notice)}>{t("common.retryFailed")}</button>}</div>;
  const success = notice.kind === "success";
  const issuedObject = notice.kind === "success" ? notice.object : undefined;
  const token = typeof issuedObject?.token === "string" ? issuedObject.token : null;
  const reviewUrl = token ? `${window.location.origin}/review/${encodeURIComponent(token)}/CL-003` : null;
  const affectedMembers = typeof issuedObject?.affectedActiveMembers === "number" ? issuedObject.affectedActiveMembers : null;
  const permissionImpact = affectedMembers !== null ? issuedObject : null;
  const permissionList = (value: unknown) => Array.isArray(value) && value.every((item) => typeof item === "string") ? value.join("、") : t("common.unknown");
  return <div className={`xj-cc-notice is-${notice.kind}`} role={success ? "status" : "alert"}>{success ? <CheckCircle2 aria-hidden="true" size={18} /> : <Clock3 aria-hidden="true" size={18} />}<div><strong>{t(success ? "common.success" : notice.kind === "processing" ? "common.processing" : "common.failure")}</strong>{notice.requestId && <small>{t("common.requestId", { id: notice.requestId })}</small>}{reviewUrl && <div className="mt-2 flex flex-wrap items-center gap-2"><input aria-label={t("common.issuedReviewLink")} readOnly value={reviewUrl} className="min-w-72 rounded border border-white/10 bg-black/20 px-2 py-1 font-mono text-xs" /><button type="button" className="xj-cc-button is-quiet" onClick={() => void navigator.clipboard.writeText(reviewUrl)}>{t("common.copyLink")}</button>{typeof issuedObject?.accessSecret === "string" && <small>{t("common.accessSecret", { secret: issuedObject.accessSecret })}</small>}</div>}{permissionImpact && <dl className="mt-2 grid gap-1 text-sm"><div><dt className="inline font-medium">{t("common.affectedMembers")}：</dt><dd className="inline">{affectedMembers}</dd></div><div><dt className="inline font-medium">{t("common.projectAssignments")}：</dt><dd className="inline">{typeof permissionImpact.projectAssignments === "number" ? permissionImpact.projectAssignments : t("common.unknown")}</dd></div>{permissionImpact.unchanged === true ? <div>{t("common.noPermissionChanges")}</div> : <><div><dt className="inline font-medium">{t("common.addedPermissions")}：</dt><dd className="inline">{permissionList(permissionImpact.addedPermissions)}</dd></div><div><dt className="inline font-medium">{t("common.removedPermissions")}：</dt><dd className="inline">{permissionList(permissionImpact.removedPermissions)}</dd></div></>}</dl>}</div></div>;
}

export function CollaborationCommercePage({ pageId, api, commercialM14Api, context }: CollaborationCommercePageProps) {
  const { t, i18n: i18nInstance } = useTranslation(collaborationCommerceNamespace);
  const route = collaborationCommerceRouteMap.get(pageId);
  if (!route) throw new Error(`Unknown collaboration commerce page: ${pageId}`);
  const isM14 = route.module === "M14";
  const activeApi = isM14 ? commercialM14Api : api;
  const Icon = moduleIcons[route.module];
  const [phase, setPhase] = useState<Phase>("checking");
  const [session, setSession] = useState<SessionContext | null>(null);
  const [snapshot, setSnapshot] = useState<CommercePageSnapshot | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dialogAction, setDialogAction] = useState<CommerceActionDefinition | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [exporting, setExporting] = useState(false);
  const mounted = useRef(true);

  const readServerState = useCallback(async (options: { readonly silent?: boolean; readonly pageToken?: string; readonly signal?: AbortSignal } = {}) => {
    if (!activeApi) return;
    if (!options.silent) setPhase("checking");
    try {
      let currentSession: SessionContext | null = null;
      if (route.audience !== "client") {
        currentSession = await activeApi.getSessionContext(options.signal);
        if (!hasPermission(currentSession, route.viewPermission)) {
          if (mounted.current) { setSession(currentSession); setRequestId(null); setPhase("denied"); }
          return;
        }
      }
      const data = await activeApi.loadPage(route, context, { pageSize: 25, query: submittedQuery || undefined, pageToken: options.pageToken }, options.signal);
      if (!mounted.current) return;
      setSession(currentSession);
      setSnapshot(data);
      setRequestId(data.requestId || null);
      setSelectedId((current) => current && data.items.some((item) => item.id === current) ? current : data.items[0]?.id ?? data.detail?.id ?? null);
      setPhase("ready");
    } catch (cause) {
      if (options.signal?.aborted || (cause instanceof Error && cause.name === "AbortError") || !mounted.current) return;
      const info = errorInfo(cause);
      setRequestId(info.requestId);
      setPhase(info.status === 401 || info.status === 403 ? "denied" : "failed");
    }
  }, [activeApi, context, route, submittedQuery]);

  useEffect(() => {
    if (!activeApi) return;
    mounted.current = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => { void readServerState({ signal: controller.signal }); }, 0);
    return () => { mounted.current = false; window.clearTimeout(timer); controller.abort(); };
  }, [activeApi, readServerState]);

  const selected = useMemo(() => snapshot?.items.find((item) => item.id === selectedId) ?? snapshot?.detail ?? null, [selectedId, snapshot]);
  const allowedActions = route.actions.filter((action) => route.audience === "client" ? action.clientSafe : Boolean(session && hasPermission(session, action.permission)));

  const runAction = useCallback(async (action: CommerceActionDefinition, payload: Readonly<Record<string, unknown>>, retryIds?: readonly string[]) => {
    setDialogAction(null);
    const actionLabel = t(action.labelKey);
    setNotice({ kind: "pending", actionLabel });
    const input = {
      targetId: action.scope === "record" ? selected?.id : undefined,
      version: typeof selected?.version === "number" ? selected.version : undefined,
      payload: retryIds ? { ids: retryIds } : payload,
    };
    try {
      const actionRoute = action.id === retryFailedAction.id ? { ...route, actions: [...route.actions, action] } : route;
      const [result] = await Promise.all([
        activeApi?.executeAction(actionRoute, action, context, input, { retryNetworkOnce: true }) ?? Promise.reject(new Error("M14 CommercialClient adapter is required")),
        new Promise<void>((resolve) => window.setTimeout(resolve, 120)),
      ]);
      if (!mounted.current) return;
      if (result.status === "partial") {
        setNotice({ kind: "partial", requestId: result.requestId, failures: result.failures, retryableIds: result.retryableIds, sourceAction: action });
        return;
      }
      if (result.status === "failed") {
        setNotice({ kind: "failure", requestId: result.requestId });
        return;
      }
      if (result.status === "processing") setNotice({ kind: "processing", requestId: result.requestId });
      await readServerState({ silent: true });
      if (mounted.current) setNotice({ kind: "success", requestId: result.requestId, object: result.object });
    } catch (cause) {
      if (!mounted.current) return;
      const info = errorInfo(cause);
      setNotice(info.status === 409 ? { kind: "conflict", requestId: info.requestId ?? undefined } : { kind: "failure", requestId: info.requestId ?? undefined });
    }
  }, [activeApi, context, readServerState, route, selected, t]);

  const refreshAfterConflict = () => {
    setNotice(null);
    void readServerState({ silent: true });
  };

  const retryPartial = (partial: Extract<Notice, { kind: "partial" }>) => {
    const retryAction = { ...retryFailedAction, endpoint: partial.sourceAction.endpoint, permission: partial.sourceAction.permission, bodyKind: partial.sourceAction.bodyKind };
    void runAction(retryAction, {}, partial.retryableIds);
  };

  if (!activeApi) return <StatePanel route={route} phase="failed" requestId={null} onRetry={() => undefined} />;
  if (phase !== "ready" || !snapshot) return <StatePanel route={route} phase={phase} requestId={requestId} onRetry={() => void readServerState()} />;

  const contextName = route.audience === "client" ? t("common.clientReview") : session?.activeWorkspace?.name ?? context.workspaceId ?? t("common.activeWorkspace");
  const detailRecord = snapshot.detail ?? selected;
  const mediaUrl = route.media ? safeMediaUrl(snapshot.detail?.mediaUrl) : null;
  const submitSearch = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setSubmittedQuery(query.trim()); };
  const downloadExport = async () => {
    if (!activeApi?.downloadExport || !route.exportEndpoint || exporting) return;
    setExporting(true);
    try {
      const result = await activeApi.downloadExport(route, context, submittedQuery || undefined);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setNotice({ kind: "success" });
    } catch (cause) {
      const info = errorInfo(cause);
      setNotice({ kind: "failure", requestId: info.requestId ?? undefined });
    } finally {
      setExporting(false);
    }
  };

  return (
    <main className="xj-cc-shell">
      <header className="xj-cc-header">
        <div className="xj-cc-heading">
          <div className="xj-cc-module-mark"><Icon aria-hidden="true" size={20} /><span>{t("common.module", { module: route.module })}</span></div>
          <h1>{t(route.titleKey)}</h1>
          <p>{t(route.descriptionKey)}</p>
        </div>
        <div className="xj-cc-context-card">
          <span>{t("common.context")}</span><strong>{contextName}</strong>
          <small>{route.audience === "client" ? t("common.clientContext") : t("common.recordCount", { count: snapshot.items.length })}</small>
        </div>
      </header>

      {notice && <NoticePanel notice={notice} onRefresh={refreshAfterConflict} onRetryFailed={retryPartial} />}

      <section className="xj-cc-command-bar" aria-label={t("common.actions")}>
        <form className="xj-cc-search" onSubmit={submitSearch}>
          <Search aria-hidden="true" size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} aria-label={t("common.searchPlaceholder")} placeholder={t("common.searchPlaceholder")} />
          <button type="submit" className="xj-cc-button is-quiet">{t("common.search")}</button>
          {submittedQuery && <button type="button" className="xj-cc-button is-quiet" onClick={() => { setQuery(""); setSubmittedQuery(""); }}>{t("common.clear")}</button>}
        </form>
        <div className="xj-cc-actions">
          {route.exportEndpoint && activeApi.downloadExport && session && route.managePermission && hasPermission(session, route.managePermission) && <button type="button" className="xj-cc-button" disabled={exporting} onClick={() => void downloadExport()}><Download aria-hidden="true" size={16} />{t(exporting ? "common.exporting" : "common.export")}</button>}
          {allowedActions.map((action) => <button type="button" key={action.id} className={`xj-cc-button ${action.confirmation === "danger" ? "is-danger" : ""}`} disabled={action.scope === "record" && !selected} onClick={() => setDialogAction(action)}>{t(action.labelKey)}</button>)}
        </div>
      </section>

      {snapshot.summary.length > 0 && <section className="xj-cc-metrics" aria-label={t("common.related")}>
        {snapshot.summary.map((metric) => <article key={metric.key}><span>{metric.label}</span><strong>{formatValue(metric.value, metric.format, i18nInstance.language, metric.currency)}</strong></article>)}
      </section>}

      {route.media && <section className="xj-cc-media-panel"><div className="xj-cc-section-title"><span>{t("common.clientPlayer")}</span><strong>{formatValue(snapshot.detail?.visibleVersion, "version", i18nInstance.language)}</strong></div>{mediaUrl ? <video controls preload="metadata" src={mediaUrl}><track kind="captions" srcLang={i18nInstance.language} label={t("common.clientPlayer")} /></video> : <p className="xj-cc-empty-inline">{t("common.unavailableMedia")}</p>}</section>}

      <div className="xj-cc-content-grid">
        <section className="xj-cc-data-panel">
          <div className="xj-cc-section-title"><div><span>{t("common.details")}</span><strong>{route.id}</strong></div>{snapshot.serverTime && <small>{t("common.serverTime", { time: formatValue(snapshot.serverTime, "datetime", i18nInstance.language) })}</small>}</div>
          {snapshot.items.length === 0 && !snapshot.detail ? <div className="xj-cc-empty"><PanelsTopLeft aria-hidden="true" /><h2>{t(route.emptyKey)}</h2><p>{t(route.descriptionKey)}</p></div> : snapshot.items.length > 0 ? (
            <div className="xj-cc-table-wrap"><table><thead><tr>{route.columns.map((column) => <th key={column.key}>{t(column.labelKey)}</th>)}</tr></thead><tbody>{snapshot.items.map((item, index) => {
              const itemKey = item.id ?? `${route.id}-${index}`;
              return <tr key={itemKey} className={item.id === selectedId ? "is-selected" : undefined} onClick={() => item.id && setSelectedId(item.id)}>{route.columns.map((column) => <td key={column.key}><span className={column.format === "status" ? "xj-cc-status" : undefined}>{formatValue(item[column.key], column.format, i18nInstance.language)}</span></td>)}</tr>;
            })}</tbody></table></div>
          ) : detailRecord ? <dl className="xj-cc-detail-grid">{route.columns.map((column) => <div key={column.key}><dt>{t(column.labelKey)}</dt><dd>{formatValue(detailRecord[column.key], column.format, i18nInstance.language)}</dd></div>)}</dl> : null}
          {snapshot.nextPageToken && <button type="button" className="xj-cc-button xj-cc-next" onClick={() => void readServerState({ silent: true, pageToken: snapshot.nextPageToken ?? undefined })}>{t("common.nextPage")}<ChevronRight aria-hidden="true" size={16} /></button>}
        </section>

        <aside className="xj-cc-evidence-panel">
          <div className="xj-cc-section-title"><div><span>{t("common.evidence")}</span><strong>{route.id}</strong></div><ShieldCheck aria-hidden="true" size={20} /></div>
          {snapshot.workflow.length > 0 ? <ol className="xj-cc-workflow">{snapshot.workflow.map((node) => <li key={node.id} className={`is-${node.status}`}><span className="xj-cc-node" /><div><strong>{node.label}</strong><small>{node.occurredAt ? formatValue(node.occurredAt, "datetime", i18nInstance.language) : t("common.status", { status: node.status })}</small></div></li>)}</ol> : <p className="xj-cc-muted">{t("common.noWorkflow")}</p>}
          <div className="xj-cc-evidence-meta"><span>{t("common.requestId", { id: snapshot.requestId || "—" })}</span>{snapshot.related && snapshot.related.length > 0 && <span>{t("common.related")} · {snapshot.related.length}</span>}</div>
        </aside>
      </div>

      {dialogAction && <ActionDialog action={dialogAction} context={context} onCancel={() => setDialogAction(null)} onConfirm={(payload) => void runAction(dialogAction, payload)} />}
    </main>
  );
}
