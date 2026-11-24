import { ArrowUpRight, Check, ChevronRight, CirclePause, ClipboardList, Clock3, Film, Image as ImageIcon, ListFilter, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AuthenticatedVideo } from "@/components/shared/AuthenticatedVideo";
import { Pagination } from "@/components/ui/Pagination";
import { ReferencePreview } from "@/xingjing_assets_storyboard/AssetReferenceGeneration";
import { canRetryTask, type GenerationTask, type GenerationTaskDetail, type GenerationTaskFilters, type GenerationWorkspaceScope, type ProductionAction, type ProductionGroup, type ProductionState } from "./contracts";
import { useQueueText } from "./queue-i18n";
import "./production-queue.css";

export const PRODUCTION_PAGE_SIZE = 12;
const groups: Array<ProductionGroup | "all"> = ["all", "working", "attention", "completed", "ended"];
const capabilities = ["image_generation", "image_edit", "inpaint", "reference_fusion", "image_upscale", "character_turnaround", "character_expressions", "video_generation", "text_to_video", "image_to_video", "first_last_video", "video_candidates", "video_extend", "video_retry", "video_stabilize", "video_upscale"];
type Translate = ReturnType<typeof useQueueText>["t"];

export function queueLocation(params?: URLSearchParams) {
  const group = groups.find((g) => g === params?.get("progress"));
  const media = params?.get("media");
  const page = Number(params?.get("page") ?? 1);
  const filters: GenerationTaskFilters = {
    group: group === "all" ? undefined : group,
    mediaType: media === "image" || media === "video" ? media : undefined,
    capability: capabilities.find((c) => c === params?.get("type")),
  };
  return { filters, offset: Number.isSafeInteger(page) && page > 0 && page <= 100000 ? (page - 1) * PRODUCTION_PAGE_SIZE : 0 };
}

function briefOf(task: GenerationTask) {
  const brief = task.request.prompt.trim();
  if (["reference", "character_turnaround", "character_expressions"].includes(task.creator?.kind ?? "")
    || /^Create a production reference image/i.test(brief) || brief.startsWith("{") || brief.startsWith("[")) return "";
  return brief;
}

function presentation(task: GenerationTask, t: Translate) {
  const info = task.creator;
  const isImage = task.request.media_type === "image";
  const brief = briefOf(task);
  let type = capabilities.includes(task.request.capability) ? t(task.request.capability) : t(isImage ? "imageJob" : "videoJob");
  if (info?.kind === "reference") type = t(info.assetKind === "character" ? "characterRef" : "reference");
  if (info?.kind === "shot") type = t(isImage ? "shotImage" : "shotVideo");
  const title = info?.shot
    ? [info.episode && t("episode", { number: info.episode }), t("shot", { number: info.shot })].filter(Boolean).join(" · ")
    : info?.subject ? [info.subject, type].join(" · ") : type;
  return { title, brief };
}

function formatTime(value: string, language: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

function StateBadge({ state }: { state: ProductionState }) {
  const { t } = useQueueText();
  const Icon = state === "succeeded" ? Check : ["checking", "waiting_stopped", "cancelling"].includes(state) ? CirclePause : Clock3;
  return <span className="xj-production-state" data-state={state}><Icon size={13} aria-hidden="true" />{t(state)}</span>;
}

function Cost({ billing }: { billing: GenerationTaskDetail["billing"] }) {
  const { t, i18n } = useQueueText();
  if (!billing) return <p className="xj-production-muted">{t("noCost")}</p>;
  const formatter = new Intl.NumberFormat(i18n.language, { style: "currency", currency: billing.currency });
  const divisor = 10 ** (formatter.resolvedOptions().maximumFractionDigits ?? 2);
  const held = billing.status === "active" ? billing.estimated_minor : 0;
  return <dl className="xj-production-cost">{[["held", held], ["charged", billing.actual_minor], ["released", billing.released_minor]].map(([key, value]) =>
    <div key={key}><dt>{t(String(key))}</dt><dd>{formatter.format(Number(value) / divisor)}</dd></div>)}</dl>;
}

function Actions({ task, state, busy, onAction }: { task: GenerationTask; state: ProductionState; busy: boolean; onAction: (action: ProductionAction) => void }) {
  const { t } = useQueueText();
  const [confirming, setConfirming] = useState(false);
  const info = task.creator;
  const action = info ? info.action ?? (info.canRetry ? "retry" : null)
    : canRetryTask(task.status) ? "retry" : task.status === "queued" ? "cancel" : null;
  const help: Partial<Record<ProductionState, string>> = {
    checking: "checkHelp", waiting_stopped: "stoppedHelp", publishing: "publishingHelp", queued: "queuedHelp",
    running: action === "stop_waiting" ? "sentHelp" : "submittedHelp", cancelling: "cancellingHelp",
    cancelled: "cancelledHelp", retrying: "queuedHelp", failed: "failedHelp", timeout: "timeoutHelp",
  };
  if (!action && !help[state]) return null;
  const repeatOriginal = state === "waiting_stopped" && action === "retry";
  const confirmLabel = action === "stop_waiting" ? "confirmStop" : action === "cancel" ? "confirmCancel" : "confirmRetry";
  return <section className="xj-production-action" data-review={["checking", "waiting_stopped", "cancelling"].includes(state)}>
    {confirming && action ? <div role="group" aria-label={t(confirmLabel)}>
      <strong>{t(confirmLabel)}</strong><p>{t(repeatOriginal ? "repeatHelp" : action === "stop_waiting" ? "stopHelp" : action === "retry" ? "retryHelp" : "cancelHelp")}</p>
      <div className="xj-production-controls">
        <button type="button" disabled={busy} className="xj-production-action-button" onClick={() => { setConfirming(false); onAction(action); }}>{t(confirmLabel)}</button>
        <button type="button" className="xj-production-link" onClick={() => setConfirming(false)}>{t("back")}</button>
      </div>
    </div> : <>
      {help[state] && <p>{t(help[state])}</p>}
      {action && <button type="button" className="xj-production-action-button" disabled={busy} onClick={() => setConfirming(true)}>{t(repeatOriginal ? "repeatOriginal" : action)}</button>}
    </>}
  </section>;
}

function Detail({ detail, scope, busy, onClose, onAction, onChoose }: {
  detail: GenerationTaskDetail; scope: GenerationWorkspaceScope; busy: boolean; onClose: () => void;
  onAction: (action: ProductionAction) => void; onChoose: (assetId: string) => void;
}) {
  const { t, i18n } = useQueueText();
  const { task, progress } = detail;
  const { title, brief } = presentation(task, t);
  const info = task.creator;
  const state = info?.state ?? task.status;
  const scopePath = `/app/workspaces/${encodeURIComponent(scope.workspaceId)}/projects/${encodeURIComponent(scope.projectId)}`;
  const board = info?.assetKind === "character";
  const sourcePage = !board ? "asset-editor" : info?.kind === "character_expressions" ? "asset-expressions" : "asset-turnaround";
  const drawing = sourcePage === "asset-turnaround" && state === "waiting_stopped"
    ? `?drawing=${info?.kind === "reference" ? "reference" : "sheet"}` : "";
  return <aside className="xj-production-panel xj-production-detail" aria-label={t("detail")}>
    <div className="xj-production-detail-top"><span>{t("detail")}</span><button type="button" className="xj-production-icon" onClick={onClose} aria-label={t("close")}><X size={17} /></button></div>
    <h2 tabIndex={-1}>{title}</h2><StateBadge state={state} />
    {info?.sourceAssetId && <a className="xj-production-source" href={`${scopePath}/${sourcePage}/${encodeURIComponent(info.sourceAssetId)}${drawing}`}>{t(drawing && info.canRetry ? "editOnBoard" : board ? "openBoard" : "openAsset")} <ArrowUpRight size={14} /></a>}
    <Actions key={`${task.task_id}:${state}:${info?.action}`} task={task} state={state} busy={busy} onAction={onAction} />
    {state === "queued" && progress.queue_position != null && <p className="xj-production-muted">{t("queuePlace", { number: progress.queue_position })}</p>}
    {progress.latest && state === "running" && <div className="xj-production-progress"><label htmlFor="xj-production-progress">{t("progress")} · {progress.latest.percent}%</label><progress id="xj-production-progress" max={100} value={progress.latest.percent} /></div>}
    {detail.generatedAssets.length > 0 && <section className="xj-production-section"><h3>{t("result")}</h3>{detail.generatedAssets.map((asset, index) => {
      const source = `/api/v1/generation-assets/${encodeURIComponent(asset.asset_id)}/content?projectId=${encodeURIComponent(scope.projectId)}`;
      const label = t("option", { number: index + 1 });
      const chosen = detail.selectedCandidate?.selected_asset_id === asset.asset_id;
      return <figure key={asset.asset_id} className="xj-production-result">
        {asset.media_type === "image" ? <ReferencePreview source={source} alt={`${title} · ${label}`} {...scope} /> : <AuthenticatedVideo source={source} {...scope} />}
        <figcaption><span>{label}</span>{info?.canChooseResult === false ? chosen && <span>{t("chosen")}</span> : <button type="button" className="xj-production-link" disabled={chosen || busy} onClick={() => onChoose(asset.asset_id)}>{t(chosen ? "chosen" : "use")}</button>}</figcaption>
      </figure>;
    })}</section>}
    <section className="xj-production-section"><h3>{t("cost")}</h3><Cost billing={detail.billing} /></section>
    {brief && <details className="xj-production-fold"><summary>{t("prompt")}</summary><p>{brief}</p></details>}
    <dl className="xj-production-dates"><div><dt>{t("start")}</dt><dd>{formatTime(task.created_at, i18n.language)}</dd></div><div><dt>{t("updated")}</dt><dd>{formatTime(task.updated_at, i18n.language)}</dd></div></dl>
    <details className="xj-production-fold xj-production-support"><summary>{t("referenceNo")}</summary><code>{task.task_id}</code></details>
  </aside>;
}

export function ProductionQueue({ scope, tasks, total, offset, filters, selected, refreshing, busy, onFilters, onPage, onRefresh, onSelect, onClose, onAction, onChoose }: {
  scope: GenerationWorkspaceScope; tasks: readonly GenerationTask[]; total: number; offset: number;
  filters: GenerationTaskFilters; selected: GenerationTaskDetail | null; refreshing: boolean; busy: boolean;
  onFilters: (filters: GenerationTaskFilters) => void; onPage: (offset: number) => void; onRefresh: () => void;
  onSelect: (task: GenerationTask) => void; onClose: () => void; onAction: (task: GenerationTask, action: ProductionAction) => void; onChoose: (assetId: string) => void;
}) {
  const { t, i18n } = useQueueText();
  const wrapper = useRef<HTMLDivElement>(null);
  const focusOnSelection = useRef(false);
  const taskId = selected?.task.task_id;
  useEffect(() => {
    if (taskId && focusOnSelection.current) {
      focusOnSelection.current = false;
      const root = wrapper.current;
      if (root && root.clientWidth <= 800) root.querySelector<HTMLElement>(".xj-production-detail h2")?.focus();
    }
  }, [taskId]);
  const filtered = Boolean(filters.group || filters.mediaType || filters.capability);
  return <div ref={wrapper} className="xj-production-grid"><section className="xj-production-panel xj-production-list" aria-label={t("list")}>
    <div className="xj-production-head"><div><h2>{t("list")}</h2><span>{t("count", { count: total })}</span></div>
      <button type="button" className="xj-production-link" onClick={onRefresh} disabled={refreshing}><RefreshCw size={14} className={refreshing ? "animate-spin" : undefined} />{t(refreshing ? "refreshing" : "refresh")}</button>
    </div>
    <div className="xj-production-filters"><div className="xj-production-groups" role="group" aria-label={t("groups")}>{groups.map((group) =>
      <button key={group} type="button" aria-pressed={(filters.group ?? "all") === group} onClick={() => onFilters({ ...filters, group: group === "all" ? undefined : group })}>{t(group)}</button>)}</div>
      <div className="xj-production-selects"><ListFilter size={15} aria-hidden="true" />
        <select aria-label={t("media")} value={filters.mediaType ?? ""} onChange={(event) => onFilters({ ...filters, mediaType: (event.target.value || undefined) as GenerationTaskFilters["mediaType"] })}><option value="">{t("allMedia")}</option>{["image", "video"].map((m) => <option key={m} value={m}>{t(m)}</option>)}</select>
        <select aria-label={t("type")} value={filters.capability ?? ""} onChange={(event) => onFilters({ ...filters, capability: event.target.value || undefined })}><option value="">{t("allTypes")}</option>{capabilities.map((key) => <option key={key} value={key}>{t(key)}</option>)}</select>
      </div>
    </div>
    {tasks.map((task) => {
      const { title, brief } = presentation(task, t);
      const isImage = task.request.media_type === "image";
      const Icon = isImage ? ImageIcon : Film;
      return <button key={task.task_id} type="button" className="xj-production-row" aria-pressed={task.task_id === selected?.task.task_id} onClick={() => {
        if (taskId === task.task_id && wrapper.current && wrapper.current.clientWidth <= 800) wrapper.current.querySelector<HTMLElement>(".xj-production-detail h2")?.focus();
        focusOnSelection.current = taskId !== task.task_id;
        onSelect(task);
      }}>
        <span className="xj-production-media"><Icon size={19} aria-hidden="true" /></span>
        <span className="xj-production-row-name"><strong>{title}</strong>{brief && <span>{brief}</span>}</span>
        <span className="xj-production-row-meta"><StateBadge state={task.creator?.state ?? task.status} /><time dateTime={task.updated_at}>{formatTime(task.updated_at, i18n.language)}</time></span>
        <ChevronRight size={15} aria-hidden="true" />
      </button>;
    })}
    {!tasks.length && <div className="xj-production-empty"><ClipboardList size={25} aria-hidden="true" /><h3>{t(filtered ? "noMatch" : "none")}</h3>{filtered && <button type="button" className="xj-production-link" onClick={() => onFilters({})}>{t("reset")}</button>}</div>}
    {total > PRODUCTION_PAGE_SIZE && <div className="xj-production-pages"><Pagination pageSize={PRODUCTION_PAGE_SIZE} total={total} offset={offset} onOffsetChange={onPage} disabled={refreshing} /></div>}
  </section>
    {selected ? <Detail key={selected.task.task_id} scope={scope} detail={selected} busy={busy} onAction={(action) => onAction(selected.task, action)} onChoose={onChoose} onClose={onClose} />
      : <aside className="xj-production-panel xj-production-empty"><ClipboardList size={26} aria-hidden="true" /><h3>{t("choose")}</h3><p>{t("chooseHelp")}</p></aside>}
  </div>;
}
