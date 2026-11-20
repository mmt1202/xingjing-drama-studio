import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { getTokenForSession } from "@/utils/auth";

import { AssetLibrary, LIBRARY_PAGE_SIZE, recallLibrary, rememberLibrary, RightsSummary } from "./AssetLibrary";
import { AssetPermissionDialog, type PermissionDraft } from "./AssetPermissionDialog";
import { useAssetLibraryTranslation } from "./asset-library-i18n";
import { AssetsStoryboardApiError, createAssetsStoryboardApi } from "./api";
import type {
  AssetActionResult,
  AssetItem,
  AssetType,
  AssetsStoryboardPort,
  BatchReceipt,
  AssetListQuery,
  RightsStatus,
  SessionContext,
  TaskReceipt,
  WorkspaceProjectScope,
} from "./contracts";
import { BatchResultPanel, ConfirmDialog, LoadingState, StatePanel, TaskSummary, WorkspaceShell, button, panel, primaryButton } from "./ui";
import { useProductionHandoffTranslation } from "./handoff-i18n";
import { useDraftProtection } from "./useDraftProtection";
import { clearProductionDraft, productionDraftSlot, type ProductionDraftScope } from "./draft-state";
import { useRecoverableDraft } from "./useRecoverableDraft";
import { DraftRecoveryNotice } from "./DraftRecoveryNotice";
import { AssetReferenceGeneration, assetHasReferenceImage } from "./AssetReferenceGeneration";
import { useAssetGenerationTranslation } from "./asset-generation-i18n";
import { assetImageCapabilities, useAssetImageModels, type AssetImageControls } from "./asset-image-models";
import { useAssetExtractionTranslation } from "./asset-extraction-i18n";
import { FrozenScriptSelect, useFrozenScriptSource, type EpisodeScriptBinding, type FrozenScriptSource } from "./FrozenScriptSelect";
import { ExtractionResults, extractionTypes, useExtractionLocation } from "./ExtractionResults";
import { assetTaskRecoveryKey, clearPendingAssetSubmission, isRecoverableAssetAction, readAssetTask, readPendingAssetSubmission, rememberAssetTask, rememberPendingAssetSubmission, type PendingAssetSubmission } from "./asset-task-recovery";

export type AssetView = "extract" | "library" | "editor" | "turnaround" | "expressions" | "market" | "market-detail";

export interface AssetsWorkspaceProps {
  readonly scope: WorkspaceProjectScope;
  readonly view?: AssetView;
  readonly assetId?: string;
  readonly projectName?: string;
  readonly api?: AssetsStoryboardPort;
  readonly onNavigate?: (view: AssetView, assetId?: string) => void;
  readonly initialScriptVersionId?: string;
  readonly initialScriptId?: string;
  readonly episodeBindings?: readonly EpisodeScriptBinding[];
  readonly onReviewScripts?: () => void;
}

const assetTypeLabels: Record<AssetType, string> = { character: "角色", scene: "场景", prop: "道具", costume: "服装", voice: "声音" };
const rightsLabels: Record<RightsStatus, string> = { missing: "缺少授权", pending: "待核验", verified: "已授权", expired: "已过期", blocked: "已阻断" };
const viewTitles: Record<Exclude<AssetView, "extract" | "library" | "editor">, [string, string]> = {
  turnaround: ["角色三视图", "基于已选角色主版本生成正面、侧面、背面、半身、全身与服装变化。"],
  expressions: ["角色表情集", "从角色主版本派生表情资产，并保留来源与版本关系。"],
  market: ["素材市场", "浏览可商用素材，核对许可范围后加入当前工作区资产库。"],
  "market-detail": ["素材详情", "查看素材预览、授权范围、价格、使用记录与项目引用。"],
};

function errorRequestId(error: unknown): string | undefined {
  return error instanceof AssetsStoryboardApiError ? error.requestId : undefined;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务暂时不可用，请重试";
}

function isForbidden(error: unknown): boolean {
  return error instanceof AssetsStoryboardApiError && error.status === 403;
}

function isConflict(error: unknown): boolean {
  return error instanceof AssetsStoryboardApiError && error.status === 409;
}

function statusTone(status: string): string {
  if (["succeeded", "verified", "frozen"].includes(status)) return "border-[#22C55E]/40 bg-[#22C55E]/10 text-[#86efac]";
  if (["failed", "blocked", "missing", "expired"].includes(status)) return "border-[#EF4444]/40 bg-[#EF4444]/10 text-[#fca5a5]";
  if (["queued", "running", "retrying", "pending"].includes(status)) return "border-[#8B5CF6]/40 bg-[#8B5CF6]/10 text-[#c4b5fd]";
  return "border-[#3A3F4E] bg-[#171922] text-[#B8BECC]";
}

function AssetCard({ asset, selected, canManage, onSelect, onOpen }: {
  readonly asset: AssetItem;
  readonly selected: boolean;
  readonly canManage: boolean;
  readonly onSelect: () => void;
  readonly onOpen: () => void;
}) {
  return <article className={`${panel} overflow-hidden ${selected ? "ring-2 ring-[#8B5CF6]" : ""}`}>
    <div className="aspect-[16/9] bg-[#171922]">
      {asset.thumbnailUrl ? <img src={asset.thumbnailUrl} alt={`${asset.name} 主参考`} className="h-full w-full object-cover" loading="lazy" /> : <div className="grid h-full place-items-center font-mono text-xs text-[#7E8494]">{asset.id}</div>}
    </div>
    <div className="p-4">
      <div className="flex items-start gap-3">
        {canManage ? <input type="checkbox" aria-label={`选择 ${asset.name}`} checked={selected} onChange={onSelect} className="mt-1 size-4 accent-[#8B5CF6]" /> : null}
        <div className="min-w-0 flex-1"><p className="text-xs text-[#8B5CF6]">{assetTypeLabels[asset.type]} · v{asset.version}</p><h3 className="mt-1 truncate font-semibold">{asset.name}</h3><code className="mt-2 block truncate text-[11px] text-[#7E8494]">{asset.id}</code></div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2"><span className={`rounded-full border px-2 py-1 text-[11px] ${statusTone(asset.rightsStatus)}`}>{rightsLabels[asset.rightsStatus]}</span><span className="rounded-full border border-[#3A3F4E] px-2 py-1 text-[11px] text-[#B8BECC]">{asset.referenceCount} 个镜头引用</span></div>
      <button type="button" className={`${button} mt-4 w-full`} onClick={onOpen}>查看与编辑</button>
    </div>
  </article>;
}

function AssetFilters({ search, type, rights, onSearch, onType, onRights, onApply }: {
  readonly search: string;
  readonly type: AssetType | "";
  readonly rights: RightsStatus | "";
  readonly onSearch: (value: string) => void;
  readonly onType: (value: AssetType | "") => void;
  readonly onRights: (value: RightsStatus | "") => void;
  readonly onApply: () => void;
}) {
  return <section className={`${panel} mb-4 grid gap-3 p-4 lg:grid-cols-[2fr_1fr_1fr_auto]`} aria-label="资产筛选">
    <label className="text-xs text-[#B8BECC]">搜索资产<input value={search} onChange={(event) => onSearch(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm" /></label>
    <label className="text-xs text-[#B8BECC]">资产类型<select value={type} onChange={(event) => onType(event.target.value as AssetType | "")} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm"><option value="">全部类型</option>{Object.entries(assetTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label className="text-xs text-[#B8BECC]">授权状态<select value={rights} onChange={(event) => onRights(event.target.value as RightsStatus | "")} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm"><option value="">全部状态</option>{Object.entries(rightsLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <button type="button" className={`${button} self-end`} onClick={onApply}>应用筛选</button>
  </section>;
}

interface AssetSourceFormProps {
  readonly source: FrozenScriptSource;
  readonly episodes?: readonly EpisodeScriptBinding[];
  readonly onReview?: () => void;
  readonly canManage: boolean;
  readonly busy: boolean;
}

function AssetExtraction({ source, episodes, onReview, canManage, busy, onRun, task, onLibrary, onOpenAsset, api, scope }: AssetSourceFormProps & {
  api: AssetsStoryboardPort;
  scope: WorkspaceProjectScope;
  onRun: (version: string, types: AssetType[], interpretNarration: boolean) => Promise<AssetActionResult | undefined>;
  task?: TaskReceipt;
  onLibrary: () => void;
  onOpenAsset: (id: string) => void;
}) {
  const { t } = useAssetExtractionTranslation();
  const [types, setTypes] = useState<AssetType[]>(["character", "scene"]);
  const [narrationChoice, setNarrationChoice] = useState<{ version: string; enabled: boolean }>();
  const location = useExtractionLocation();
  const [completed, setCompleted] = useState<{ version: string; selection: string; result: AssetActionResult }>();
  const selectedTypes = extractionTypes.filter((kind) => types.includes(kind));
  const reading = source.choices.find((item) => item.id === source.value)?.readingScript ?? false;
  const includesPeopleOrPlaces = types.includes("character") || types.includes("scene");
  const interpretNarration = includesPeopleOrPlaces && (narrationChoice?.version === source.value ? narrationChoice.enabled : reading);
  const needsNarration = reading && includesPeopleOrPlaces && !interpretNarration;
  const selection = JSON.stringify([source.value, selectedTypes, interpretNarration]);
  const result = completed?.selection === selection ? completed.result : undefined;
  async function submit() {
    if (!canManage || busy || !source.value || !selectedTypes.length || needsNarration || result) return;
    const receipt = await onRun(source.value, selectedTypes, interpretNarration);
    if (receipt?.batch || receipt?.task) {
      setCompleted({ version: source.value, selection, result: receipt });
      if (receipt.batch) location.select(receipt.batch.operationId);
    }
  }
  function chooseSource(version: string) {
    source.select(version);
    location.select(undefined, undefined, 1, version);
  }
  return <><section className={`${panel} max-w-4xl p-6`}>
    <FrozenScriptSelect source={source} onSelect={chooseSource} episodes={episodes} disabled={busy} onReview={onReview} />
    <fieldset className="mt-5" disabled={busy || !canManage}>
      <legend className="text-sm">{t("scope")}</legend>
      <div className="mt-3 flex flex-wrap gap-3">{extractionTypes.map((kind) =>
        <label key={kind} className="rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm">
          <input type="checkbox" className="mr-2 accent-[#8B5CF6]" checked={types.includes(kind)}
            onChange={() => setTypes((current) => current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind])} />{t(kind)}
        </label>)}</div>
      <label className="mt-4 block text-sm"><input type="checkbox" className="mr-2 accent-[#8B5CF6]" disabled={!source.value || !includesPeopleOrPlaces} checked={interpretNarration} onChange={(event) => setNarrationChoice({ version: source.value, enabled: event.target.checked })} />{t("narration")}</label>
      {reading ? <p className="mt-2 text-sm text-violet-200">{t("reading")}</p> : null}
      <p className="mt-2 text-sm leading-6 text-[#B8BECC]">{t("scopeHint")}</p>
    </fieldset>
    {!canManage ? <p className="mt-3 text-sm text-amber-200">{t("manageDenied")}</p> : null}
    <div className="mt-5 flex flex-wrap gap-3">
      <button type="button" className={primaryButton} disabled={!canManage || busy || !source.value || !types.length || needsNarration || Boolean(result)} onClick={() => void submit()}>{t(busy ? "busy" : "submit")}</button>
      <button type="button" className={button} onClick={onLibrary} disabled={busy}>{t("library")}</button>
    </div>
    {result?.task ? <TaskSummary task={task?.taskId === result.task.taskId ? task : result.task} message={t("queued")} /> : null}
  </section>
    {source.value ? <ExtractionResults key={source.value} api={api} scope={scope} version={source.value} location={location}
      live={completed?.version === source.value ? completed.result.batch : undefined} busy={busy} onOpen={onOpenAsset} /> : null}
  </>;
}

function ManualAssetCreate({ canManage, busy, onCreate, source, episodes, onReview, onDirty }: AssetSourceFormProps & {
  onCreate: (payload: Record<string, unknown>) => void; onDirty: (dirty: boolean) => void;
}) {
  const { t } = useAssetExtractionTranslation();
  const { t: libraryText } = useAssetLibraryTranslation();
  const [kind, setKind] = useState<AssetType>("character");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  useEffect(() => {
    onDirty(Boolean(canManage && (name || description || kind !== "character")));
    return () => onDirty(false);
  }, [canManage, name, description, kind, onDirty]);
  const field = "mt-2 block w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3";
  return <details className={`${panel} mb-5 p-5`}>
    <summary className="cursor-pointer font-semibold text-[#F5F6FA]">{libraryText("manual")}</summary>
    <div className="mt-4 grid gap-3 md:grid-cols-2">
      <FrozenScriptSelect source={source} episodes={episodes} disabled={busy} onReview={onReview} />
      <label className="text-sm text-[#B8BECC]">{t("kind")}
        <select disabled={busy || !canManage} value={kind} onChange={(event) => setKind(event.target.value as AssetType)} className={field}>
          {extractionTypes.map((value) => <option key={value} value={value}>{t(value)}</option>)}
        </select>
      </label>
      <label className="text-sm text-[#B8BECC]">{t("name")}<input disabled={busy || !canManage} value={name} onChange={(event) => setName(event.target.value)} className={field} /></label>
      <label className="text-sm text-[#B8BECC]">{t("description")}<input disabled={busy || !canManage} value={description} onChange={(event) => setDescription(event.target.value)} className={field} /></label>
    </div>
    <button type="button" disabled={!canManage || busy || !source.value || !name.trim()} onClick={() => onCreate({ frozenSnapshotId: source.value, kind, name: name.trim(), metadata: { description } })} className={`${primaryButton} mt-4 disabled:opacity-50`}>{t(busy ? "creating" : "create")}</button>
  </details>;
}

function MetadataEditor({ asset, values, onChange }: { readonly asset: AssetItem; readonly values: Record<string, unknown>; readonly onChange: (key: string, value: string) => void }) {
  const configs: Record<AssetType, Array<[string, string]>> = {
    character: [["appearance", "角色外观"], ["personality", "角色性格"], ["voiceAssetId", "绑定声音资产 ID"]],
    scene: [["environment", "场景环境"], ["lighting", "场景光线"], ["timeOfDay", "场景时段"]],
    prop: [["purpose", "道具用途"], ["material", "道具材质"], ["continuity", "连续性要求"]],
    costume: [["setting", "服装设定"], ["characterIds", "适用角色 ID"], ["continuity", "换装连续性"]],
    voice: [["timbre", "声音音色"], ["language", "声音语言"], ["emotion", "情绪范围"]],
  };
  return <div className="grid gap-4 md:grid-cols-2">{configs[asset.type].map(([key, label]) => <label key={key} className="text-sm text-[#B8BECC]">{label}<input value={typeof values[key] === "string" ? values[key] : ""} onChange={(event) => onChange(key, event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label>)}</div>;
}

interface ConfirmState { readonly kind: "primary" | "restore"; readonly id?: string; readonly label: string }

type RightsDraft = PermissionDraft;
interface AssetDraft { baseVersion: number; currentVersionId: string; name: string; description: string; metadata: Record<string, unknown>; rights: RightsDraft; proofPending: boolean }
function validAssetDraft(value: unknown): value is AssetDraft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Partial<AssetDraft>;
  return Number.isSafeInteger(draft.baseVersion) && typeof draft.currentVersionId === "string" && typeof draft.name === "string" && typeof draft.description === "string"
    && Boolean(draft.metadata && typeof draft.metadata === "object" && !Array.isArray(draft.metadata)) && typeof draft.proofPending === "boolean"
    && Boolean(draft.rights && [draft.rights.holder, draft.rights.licenseScope, draft.rights.proofObjectKey, draft.rights.proofSha256, draft.rights.validTo].every((item) => typeof item === "string"));
}

function MarketReusePanel({ asset, busy, onCreate }: {
  readonly asset: AssetItem;
  readonly busy: boolean;
  readonly onCreate: (payload: Record<string, unknown>) => void;
}) {
  const [projectName, setProjectName] = useState(`${asset.name} 副本`);
  const [commercialUse, setCommercialUse] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const inheritableScopes = Array.isArray(asset.metadata.inheritableScopes)
    ? asset.metadata.inheritableScopes.filter((item): item is string => typeof item === "string")
    : [];
  const allowFork = asset.metadata.allowFork === true;
  const commercialAllowed = asset.metadata.commercialUse === true;
  const priceMinor = typeof asset.metadata.priceMinor === "number" ? asset.metadata.priceMinor : 0;
  const create = () => {
    onCreate({ projectName: projectName.trim(), intendedCommercialUse: commercialUse, requestedInheritableScopes: inheritableScopes, sourceVersionId: asset.currentVersionId });
    setConfirming(false);
  };
  return <section className={`${panel} p-5`}><h2 className="font-semibold">授权复用</h2><p className="mt-2 text-sm text-[#B8BECC]">创建操作走市场正式 Fork：锁定当前市场版本和源版本，并保存授权、分成与来源血缘。</p><dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><dt className="text-[#7E8494]">价格（最小计价单位）</dt><dd className="mt-1 font-mono text-[#F5F6FA]">{priceMinor}</dd></div><div><dt className="text-[#7E8494]">当前工作区使用记录</dt><dd className="mt-1 text-[#F5F6FA]">{asset.referenceCount} 次</dd></div><div><dt className="text-[#7E8494]">市场版本</dt><dd className="mt-1 font-mono text-[#F5F6FA]">v{asset.version}</dd></div><div><dt className="text-[#7E8494]">源版本</dt><dd className="mt-1 truncate font-mono text-[#F5F6FA]">{asset.currentVersionId}</dd></div></dl><label className="mt-4 block text-xs text-[#B8BECC]">新项目名称<input value={projectName} onChange={(event) => setProjectName(event.target.value)} className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2" /></label><label className="mt-3 block text-xs text-[#B8BECC]"><input type="checkbox" checked={commercialUse} disabled={!commercialAllowed} onChange={(event) => setCommercialUse(event.target.checked)} className="mr-2 accent-[#8B5CF6]" />用于商业项目{commercialAllowed ? "" : "（当前许可不允许）"}</label><p className="mt-3 text-xs text-[#7E8494]">可继承范围：{inheritableScopes.join("、") || "无"}</p><button type="button" className={`${primaryButton} mt-4 w-full`} disabled={busy || !allowFork || !projectName.trim()} onClick={() => setConfirming(true)}>{busy ? "正在创建…" : allowFork ? "创建授权副本" : "当前素材禁止 Fork"}</button><ConfirmDialog open={confirming} title="确认创建授权副本" detail={`将锁定市场 v${asset.version} 与源版本 ${asset.currentVersionId}，并按当前许可创建项目。`} confirmLabel="确认创建副本" busy={busy} onCancel={() => setConfirming(false)} onConfirm={create} /></section>;
}

function AssetEditor({ asset, canManage, busy, generation, task, ownerProject, conflict, marketDetail, onSave, onAction, onReload, onRights, onDerivative, onDirty, draftScope }: {
  readonly generation: AssetImageControls;
  readonly asset: AssetItem;
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly task?: TaskReceipt;
  readonly ownerProject: boolean;
  readonly onDirty: (value: boolean) => void;
  readonly draftScope: ProductionDraftScope;
  readonly conflict?: AssetsStoryboardApiError;
  readonly marketDetail: boolean;
  readonly onSave: (payload: Record<string, unknown>, version: number) => void;
  readonly onAction: (action: string, payload: Record<string, unknown>, version: number) => void;
  readonly onReload: () => void;
  readonly onRights: (seed?: PermissionDraft) => void;
  readonly onDerivative: (view: "turnaround" | "expressions") => void;
}) {
  const [name, setName] = useState(asset.name);
  const [description, setDescription] = useState(asset.description ?? "");
  const [metadata, setMetadata] = useState<Record<string, unknown>>(() => ({ ...asset.metadata }));
  const [rights, setRights] = useState<RightsDraft>({ holder: asset.rights?.holder ?? "", licenseScope: asset.rights?.licenseScope ?? "owner_project_only", proofObjectKey: asset.rights?.proofUploadId ?? "", proofSha256: "", validTo: asset.rights?.validTo ?? "" });
  const [confirm, setConfirm] = useState<ConfirmState>();
  const { t: libraryText } = useAssetLibraryTranslation();
  const [proofReselect, setProofReselect] = useState(false);
  const [baseVersion] = useState(asset.version);
  const [baseCurrentVersionId] = useState(asset.currentVersionId);
  const [baseSourceVersion] = useState(() => JSON.stringify([asset.version, asset.currentVersionId]));
  const [initial] = useState(() => ({ name, description, metadata, rights }));
  const { t } = useProductionHandoffTranslation();
  const dirty = canManage && !marketDetail && (proofReselect || JSON.stringify({ name, description, metadata, rights }) !== JSON.stringify(initial));
  const recovery = useRecoverableDraft({ scope: draftScope, kind: "asset", objectId: asset.id, sourceVersion: baseSourceVersion, currentSourceVersion: JSON.stringify([asset.version, asset.currentVersionId]), dirty,
    value: { baseVersion, currentVersionId: baseCurrentVersionId, name, description, metadata, rights, proofPending: proofReselect }, validate: validAssetDraft });
  useEffect(() => {
    onDirty(dirty || Boolean(recovery.pending));
    return () => onDirty(false);
  }, [dirty, recovery.pending, onDirty]);
  const confirmAction = () => { if (!confirm) return; onAction(confirm.kind === "primary" ? "setPrimaryVersion" : "restoreVersion", { versionId: confirm.id }, baseVersion); setConfirm(undefined); };
  const canEdit = canManage && !marketDetail && !recovery.pending;
  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
    <section className={`${panel} p-6`}>
      {recovery.pending ? <DraftRecoveryNotice canResume={canManage && !marketDetail && recovery.canResume && recovery.pending.value.baseVersion === asset.version && recovery.pending.value.currentVersionId === asset.currentVersionId} busy={busy} value={recovery.pending} onResume={() => recovery.resume((value) => { if (value.baseVersion !== asset.version || value.currentVersionId !== asset.currentVersionId) return; setName(value.name); setDescription(value.description); setMetadata(value.metadata); setRights(value.rights); setProofReselect(value.proofPending); })} onDiscard={recovery.clear} /> : null}
      {recovery.storageUnavailable ? <p role="alert" className="mb-4 text-xs text-amber-200">{t("storageUnavailable")}</p> : null}
      {proofReselect ? <p role="status" className="mb-4 text-xs text-amber-200">{t("proofReselect")}</p> : null}
      <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm text-violet-300">{libraryText(asset.type)}</p><h2 className="mt-1 text-xl font-semibold">{asset.name}</h2></div>
        <details className="text-xs text-slate-400"><summary className="cursor-pointer">{libraryText("details")}</summary><code className="mt-2 block">{asset.id}</code></details></div>
      <fieldset disabled={busy || !canEdit} className="mt-6 grid gap-4"><label className="text-sm text-[#B8BECC]">资产名称<input disabled={busy || !canManage} value={name} onChange={(event) => setName(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label><label className="text-sm text-[#B8BECC]">资产说明<textarea disabled={busy || !canEdit} value={description} onChange={(event) => setDescription(event.target.value)} rows={4} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label><MetadataEditor asset={asset} values={metadata} onChange={(key, value) => setMetadata((current) => ({ ...current, [key]: value }))} /></fieldset>
      {marketDetail ? <StatePanel title="市场素材为只读版本" detail="该素材的授权、价格与复用规则由市场服务端维护。导入项目或创建 Fork 必须走 M13 的正式授权流程，不能在这里伪造资产副本。" tone="warning" /> : <button type="button" className={`${primaryButton} mt-6`} disabled={!canEdit || busy || !name.trim()} onClick={() => onSave({ name: name.trim(), description, metadata }, baseVersion)}>{generation.activeAction === "updateAsset" ? "保存中…" : "保存资产"}</button>}
      {!marketDetail ? <AssetReferenceGeneration asset={asset} generation={generation} ownerProject={ownerProject} canManage={canEdit} busy={busy} draftBlocked={dirty || Boolean(recovery.pending) || asset.version !== baseVersion} task={task} onGenerate={() => onAction("generateReferenceImage", { sourceVersionId: baseCurrentVersionId }, baseVersion)} onAdopt={(generatedAssetId) => onAction("adoptGenerationOutput", { generatedAssetId }, baseVersion)} /> : null}
      {!marketDetail && asset.type === "character" ? <div className="mt-4 flex flex-wrap gap-2"><button type="button" className={button} onClick={() => onDerivative("turnaround")}>生成角色三视图</button><button type="button" className={button} onClick={() => onDerivative("expressions")}>生成角色表情集</button></div> : null}
      {conflict ? <StatePanel title="版本冲突" detail="当前版本已被其他成员更新。你的输入仍保留，可以读取最新版本或另存新版本。" requestId={conflict.requestId} tone="warning" /> : null}
      {conflict ? <p className="mt-3 text-xs leading-6 text-slate-400">{t("saveConflict")}</p> : null}
      {conflict ? <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={button} onClick={onReload}>重新读取服务端版本</button><button type="button" className={primaryButton} onClick={() => onAction("createVersion", { name: name.trim(), description, metadata }, baseVersion)}>另存为新版本</button></div> : null}
    </section>
    <aside className="space-y-4">
      {marketDetail ? <MarketReusePanel asset={asset} busy={busy} onCreate={(payload) => onAction("licenseMarketAsset", payload, baseVersion)} /> : null}
      <section className={`${panel} p-5`}><h2 className="font-semibold">{libraryText("versions")}</h2><ol className="mt-4 space-y-3">{asset.versions.map((version) => <li key={version.id} className="rounded-[12px] border border-[#2A2E3A] bg-[#171922] p-3"><div className="flex items-center justify-between"><strong>v{version.versionNo}</strong><span className={`rounded-full border px-2 py-1 text-[11px] ${statusTone(version.status)}`}>{libraryText(version.id === asset.currentVersionId ? "current" : "candidate")}</span></div><div className="mt-3 flex gap-2"><button type="button" className={button} disabled={!canEdit || busy} aria-label={`设 v${version.versionNo} 为主版本`} onClick={() => setConfirm({ kind: "primary", id: version.id, label: `v${version.versionNo}` })}>设为主版本</button><button type="button" className={button} disabled={!canEdit || busy} onClick={() => setConfirm({ kind: "restore", id: version.id, label: `v${version.versionNo}` })}>恢复</button></div></li>)}</ol></section>
      {!marketDetail ? <RightsSummary asset={asset} editable={canManage && ownerProject}
        disabled={busy || Boolean(recovery.pending) || name !== initial.name || description !== initial.description || JSON.stringify(metadata) !== JSON.stringify(initial.metadata)}
        onOpen={() => onRights(proofReselect || JSON.stringify(rights) !== JSON.stringify(initial.rights) ? rights : undefined)} /> : null}
    </aside>
    <ConfirmDialog open={Boolean(confirm)} title={confirm?.kind === "primary" ? "确认设为主版本" : "确认恢复资产版本"} detail={confirm?.kind === "primary" ? `设为主版本会影响 ${asset.referenceCount} 个镜头引用，请确认下游影响。` : "恢复操作会创建新的资产版本，不覆盖历史记录。"} confirmLabel={confirm?.kind === "primary" ? "确认设为主版本" : `确认恢复 ${confirm?.label}`} busy={busy} onCancel={() => setConfirm(undefined)} onConfirm={confirmAction} />
  </div>;
}

function CharacterDerivative({ asset, mode, canManage, busy, generation, ownerProject, onRun, onGenerateReference, onAdopt, task }: { readonly generation: AssetImageControls; readonly asset?: AssetItem; readonly mode: "turnaround" | "expressions"; readonly ownerProject: boolean; readonly canManage: boolean; readonly busy: boolean; readonly onRun: (payload: Record<string, unknown>) => void; readonly onGenerateReference: () => void; readonly onAdopt: (generatedAssetId: string) => void; readonly task?: TaskReceipt }) {
  const [values, setValues] = useState(mode === "turnaround" ? "正面,侧面,背面,半身,全身" : "喜悦,愤怒,悲伤,惊讶");
  const { t } = useAssetGenerationTranslation();
  if (!asset) return <StatePanel title="未找到角色资产" detail="请从资产库选择一个角色后再进入此页面。" />;
  const hasReference = assetHasReferenceImage(asset);
  const working = Boolean(task && ["queued", "running", "retrying", "cancelling"].includes(task.status));
  const action = mode === "turnaround" ? "generateTurnaround" : "generateExpressions";
  const available = generation.models.supports(assetImageCapabilities[action]);
  return <section className={`${panel} max-w-4xl p-6`}><p className="text-xs text-[#8B5CF6]">角色稳定 ID</p><code className="text-xs text-[#7E8494]">{asset.id}</code><h2 className="mt-4 text-xl font-semibold">{asset.name}</h2>
    {!hasReference ? <p role="status" className="mt-4 text-sm text-amber-200">{t("firstImage")}</p> : null}
    <label className="mt-5 block text-sm">{mode === "turnaround" ? "需要生成的视图（逗号分隔）" : "需要生成的表情（逗号分隔）"}<textarea rows={3} value={values} onChange={(event) => setValues(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3" /></label>
    {hasReference && !available ? <p role="status" className="mt-3 text-sm text-amber-200">{t(generation.models.notice ?? "noDerivative")}</p> : null}
    <button type="button" className={`${primaryButton} mt-5`} disabled={!canManage || busy || generation.pending || !available || working || !hasReference || !values.trim()} onClick={() => onRun({ items: values.split(",").map((item) => item.trim()).filter(Boolean), sourceVersionId: asset.currentVersionId })}>{generation.activeAction === action ? t("submitting") : mode === "turnaround" ? "生成三视图版本" : "生成表情集版本"}</button>
    <AssetReferenceGeneration asset={asset} generation={generation} ownerProject={ownerProject} canManage={canManage} busy={busy} task={task} onGenerate={onGenerateReference} onAdopt={onAdopt} />
  </section>;
}

export function AssetsWorkspace(props: AssetsWorkspaceProps) {
  const sessionEpoch = useAuthStore((auth) => auth.sessionEpoch);
  const api = useMemo(() => props.api ?? createAssetsStoryboardApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }), [props.api, sessionEpoch]);
  const scopeKey = JSON.stringify([sessionEpoch, props.scope.workspaceId, props.scope.projectId, props.scope.episodeId, props.initialScriptVersionId, props.initialScriptId]);
  return <AssetWorkspaceSession key={scopeKey} {...props} api={api} sessionEpoch={sessionEpoch} />;
}

function AssetWorkspaceSession({ scope, view = "library", assetId, projectName, api, onNavigate, initialScriptVersionId, initialScriptId, episodeBindings, onReviewScripts, sessionEpoch }: AssetsWorkspaceProps & { api: AssetsStoryboardPort; sessionEpoch: string }) {
  const [localNavigation, setLocalNavigation] = useState<{ readonly source: AssetView; readonly target: AssetView; readonly assetId?: string }>();
  const activeView = localNavigation?.source === view ? localNavigation.target : view;
  const targetAssetId = localNavigation?.source === view ? localNavigation.assetId ?? assetId : assetId;
  const detailView = ["editor", "turnaround", "expressions"].includes(activeView);
  const exactDetail = detailView && Boolean(targetAssetId) && typeof api.getAsset === "function";
  const detailKey = JSON.stringify([scope.workspaceId, scope.projectId ?? null, targetAssetId ?? null]);
  const [detail, setDetail] = useState<{ key: string; loading: boolean; asset?: AssetItem; error?: unknown }>();
  const loadSequence = useRef(0);
  const loadAbort = useRef<AbortController | undefined>(undefined);
  const { t: handoff } = useProductionHandoffTranslation();
  const { t: generationText } = useAssetGenerationTranslation();
  const { t: extractionText } = useAssetExtractionTranslation();
  const [sourceError, setSourceError] = useState<{ message: string; requestId?: string; assetId?: string; insufficient?: boolean }>();
  const [pendingRecovery, setPendingRecovery] = useState<{ key: string; operation?: PendingAssetSubmission }>();
  const [taskStorageUnavailable, setTaskStorageUnavailable] = useState(false);
  const [draftBlocked, setDraftBlocked] = useState(false), [draftReset, setDraftReset] = useState(0), [discardConfirm, setDiscardConfirm] = useState(false);
  const operationLatch = useRef(false);
  const [context, setContext] = useState<SessionContext>();
  const [assets, setAssets] = useState<readonly AssetItem[]>();
  const [selectedAssetIds, setSelectedAssetIds] = useState<readonly string[]>([]);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const { t: libraryText } = useAssetLibraryTranslation();
  const browseKey = JSON.stringify(["asset-library", sessionEpoch, scope.workspaceId, scope.projectId, scope.episodeId]);
  const [browse] = useState(() => view === "library" ? recallLibrary(browseKey) : { query: {}, offset: 0, focusAsset: undefined });
  const [activeQuery, setActiveQuery] = useState<AssetListQuery>(browse.query);
  const [libraryOffset, setLibraryOffset] = useState(browse.offset);
  const [libraryTotal, setLibraryTotal] = useState(0);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [permission, setPermission] = useState<{ asset: AssetItem; seed?: PermissionDraft }>();
  const batchNames = useRef(new Map<string, string>());
  const [search, setSearch] = useState(""); const [type, setType] = useState<AssetType | "">(""); const [rights, setRights] = useState<RightsStatus | "">("");
  const [error, setError] = useState<unknown>(); const [forbidden, setForbidden] = useState<AssetsStoryboardApiError>(); const [conflict, setConflict] = useState<AssetsStoryboardApiError>();
  const [activeAction, setActiveAction] = useState<string>();
  const busy = activeAction !== undefined;
  const [batch, setBatch] = useState<BatchReceipt>(); const [task, setTask] = useState<TaskReceipt>(); const [taskOwnerId, setTaskOwnerId] = useState<string>(); const [message, setMessage] = useState("");
  const load = useCallback(async (query = activeQuery, append = false, offset = libraryOffset) => {
    loadAbort.current?.abort();
    const controller = new AbortController();
    loadAbort.current = controller;
    const sequence = ++loadSequence.current;
    const active = () => !controller.signal.aborted && sequence === loadSequence.current;
    setError(undefined); setForbidden(undefined);
    if (activeView === "library") setLibraryLoading(true);
    if (exactDetail) setDetail((current) => current?.key === detailKey ? { ...current, loading: true, error: undefined } : { key: detailKey, loading: true });
    try {
      const session = await api.getSessionContext(scope, controller.signal);
      if (!active()) return;
      if (!session.permissions.includes("asset.view")) { setForbidden(new AssetsStoryboardApiError(403, "PERMISSION_DENIED", "没有资产查看权限")); setAssets([]); setDetail(undefined); return; }
      setContext(session);
      if (activeView === "extract") { setAssets([]); return; }
      if (exactDetail && targetAssetId && api.getAsset) {
        const asset = await api.getAsset(scope, targetAssetId, controller.signal);
        if (active()) { setDetail({ key: detailKey, loading: false, asset }); setAssets((current) => current ?? []); }
        return;
      }
      const pageQuery = {
        ...query,
        pageToken: activeView === "library" ? String(offset) : append ? nextToken ?? undefined : undefined,
        pageSize: activeView === "library" ? LIBRARY_PAGE_SIZE : undefined,
        marketItemId: activeView === "market-detail" ? targetAssetId : undefined,
      };
      let result = activeView === "market" || activeView === "market-detail" ? await api.listMarketAssets(scope, pageQuery, controller.signal) : await api.listAssets(scope, pageQuery, controller.signal);
      if (!active()) return;
      if (activeView === "library") {
        let total = result.total ?? offset + result.items.length + (result.nextToken ? 1 : 0);
        const lastOffset = Math.max(0, Math.floor((total - 1) / LIBRARY_PAGE_SIZE) * LIBRARY_PAGE_SIZE);
        if (offset > lastOffset) {
          result = await api.listAssets(scope, { ...pageQuery, pageToken: String(lastOffset) }, controller.signal);
          if (!active()) return;
          setLibraryOffset(lastOffset);
          rememberLibrary(browseKey, { query, offset: lastOffset });
          total = result.total ?? lastOffset + result.items.length;
        }
        setLibraryTotal(total);
        setSelectedAssetIds((ids) => ids.filter((id) => result.items.some((asset) => asset.id === id && asset.rightsStatus === "pending")));
      }
      setAssets((current) => append && current ? [...current, ...result.items] : result.items); setNextToken(result.nextToken);
    } catch (cause) {
      if (!active()) return;
      if (isForbidden(cause)) { setForbidden(cause as AssetsStoryboardApiError); setAssets([]); setDetail(undefined); setContext(undefined); }
      else if (exactDetail) setDetail((current) => ({ key: detailKey, loading: false, asset: current?.key === detailKey ? current.asset : undefined, error: cause }));
      else setError(cause);
    } finally { if (active() && activeView === "library") setLibraryLoading(false); }
  }, [browseKey, libraryOffset, activeQuery, activeView, api, detailKey, exactDetail, targetAssetId, nextToken, scope]);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => { window.clearTimeout(timer); loadAbort.current?.abort(); loadSequence.current += 1; };
  }, [api, scope.workspaceId, scope.projectId, activeView, targetAssetId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!task) return;
    const needsOutputs = task.status === "succeeded" && task.outputAssetIds === undefined;
    if (!needsOutputs && !["queued", "running", "retrying", "cancelling"].includes(task.status)) return;
    const controller = new AbortController();
    const refresh = () => {
      void api.getGenerationTask(scope, task, controller.signal)
        .then((current) => { if (!controller.signal.aborted) setTask((previous) => previous?.taskId === task.taskId ? current : previous); })
        .catch((cause) => {
          if (!controller.signal.aborted) setError(cause);
        });
    };
    if (needsOutputs) { refresh(); return () => controller.abort(); }
    const timer = window.setInterval(refresh, 2_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [api, scope, task]);
  const canManage = context?.permissions.includes("asset.manage") ?? false;
  const source = useFrozenScriptSource(api, scope, Boolean(context) && (activeView === "extract" || activeView === "library"), initialScriptVersionId, initialScriptId);
  const sourceProps = { source, episodes: episodeBindings, onReview: onReviewScripts, canManage: source.canManage ?? canManage, busy };
  const currentAsset = exactDetail
    ? detail?.key === detailKey ? detail.asset : undefined
    : targetAssetId ? assets?.find((item) => item.id === targetAssetId)
    : (activeView === "market-detail" || activeView === "turnaround" || activeView === "expressions" ? assets?.[0] : undefined);
  const taskStorageKey = context && currentAsset ? assetTaskRecoveryKey(context.user.id, scope.workspaceId, scope.projectId ?? currentAsset.projectId ?? "", currentAsset.id, sessionEpoch) : undefined;
  const currentAssetId = currentAsset?.id;
  const restoredSubmission = useMemo(() => {
    const saved = taskStorageKey ? readPendingAssetSubmission(taskStorageKey) : undefined;
    return saved?.targetId === currentAssetId ? saved : undefined;
  }, [taskStorageKey, currentAssetId]);
  const pendingSubmission = pendingRecovery?.key === taskStorageKey ? pendingRecovery?.operation : restoredSubmission;
  const [receiptCheck, setReceiptCheck] = useState<{ id: string; missing: boolean }>();
  const [receiptRevision, recheckReceipt] = useState(0);
  const missingReceipt = Boolean(pendingSubmission && receiptCheck?.id === pendingSubmission.idempotencyKey && receiptCheck.missing);
  const editingLocked = Boolean(pendingSubmission && !missingReceipt);
  const protection = useDraftProtection(draftBlocked || busy || editingLocked);
  const projectId = scope.projectId ?? currentAsset?.projectId;
  const models = useAssetImageModels(api, scope.workspaceId, detailView && currentAsset && currentAsset.type !== "voice" ? projectId : undefined);
  const generation = { models, activeAction, pending: Boolean(pendingSubmission), canManageBilling: Boolean(context?.permissions.includes("billing.manage")), billingHref: sourceError?.assetId === currentAssetId && sourceError?.insufficient && context?.permissions.includes("billing.view") ? `/app/workspaces/${encodeURIComponent(scope.workspaceId)}/billing` : undefined, rejection: sourceError?.assetId && sourceError.assetId === currentAssetId ? sourceError.message : undefined };
  const assetRevision = currentAsset?.version;
  useEffect(() => {
    if (busy || !taskStorageKey || !projectId || !pendingSubmission || pendingSubmission.action === "adoptGenerationOutput" || !api.getAssetGenerationReceipt) return;
    const original = pendingSubmission;
    const version = original.payload.sourceVersionId;
    if (typeof version !== "string") return;
    const controller = new AbortController();
    const lookupScope = { workspaceId: scope.workspaceId, projectId };
    void api.getAssetGenerationReceipt(lookupScope, original.targetId, version, original.idempotencyKey, controller.signal).then(async (receipt) => {
      if (controller.signal.aborted) return;
      if (!receipt) {
        if (assetRevision !== undefined && original.version !== assetRevision) {
          setTaskStorageUnavailable(!clearPendingAssetSubmission(taskStorageKey));
          setPendingRecovery({ key: taskStorageKey });
        } else setReceiptCheck({ id: original.idempotencyKey, missing: true });
        return;
      }
      const detail = await api.getGenerationTask(lookupScope, receipt, controller.signal);
      if (controller.signal.aborted) return;
      setTask(detail); setTaskOwnerId(original.targetId);
      if (rememberAssetTask(taskStorageKey, { ...detail, projectId })) {
        setTaskStorageUnavailable(!clearPendingAssetSubmission(taskStorageKey));
        setPendingRecovery({ key: taskStorageKey });
      } else setTaskStorageUnavailable(true);
    }).catch(() => { if (!controller.signal.aborted) setReceiptCheck({ id: original.idempotencyKey, missing: false }); });
    return () => controller.abort();
  }, [api, assetRevision, busy, pendingSubmission, projectId, receiptRevision, scope.workspaceId, taskStorageKey]);
  useEffect(() => {
    if (!taskStorageKey || !currentAsset) return;
    const retained = readAssetTask(taskStorageKey);
    if (!retained) return;
    const controller = new AbortController();
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      setTask(retained); setTaskOwnerId(currentAsset.id);
      try {
        const latest = await api.getGenerationTask(scope, retained, controller.signal);
        if (!controller.signal.aborted) setTask((previous) => previous?.taskId === retained.taskId ? latest : previous);
      } catch (cause) { if (!controller.signal.aborted) setError(cause); }
    });
    return () => controller.abort();
  }, [api, taskStorageKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!taskStorageKey || !task || !currentAsset || currentAsset.id !== taskOwnerId) return;
    let active = true;
    const saved = rememberAssetTask(taskStorageKey, { ...task, projectId: task.projectId ?? scope.projectId ?? currentAsset.projectId });
    void Promise.resolve().then(() => { if (active) setTaskStorageUnavailable(!saved); });
    return () => { active = false; };
  }, [taskStorageKey, task, taskOwnerId, currentAsset, scope.projectId]);
  const navigate = (next: AssetView, id?: string) => { if (!protection.canNavigate()) return; if (onNavigate) onNavigate(next, id); else setLocalNavigation({ source: view, target: next, assetId: id }); };
  const run = async (action: string, payload: Record<string, unknown> = {}, target: Pick<AssetItem, "id" | "projectId"> & Partial<Pick<AssetItem, "version">> | undefined = currentAsset, version = target?.version, original?: PendingAssetSubmission) => {
    if (operationLatch.current || pendingSubmission && original !== pendingSubmission && (!missingReceipt || isRecoverableAssetAction(action))) return;
    const ownerProjectId = !scope.projectId && target?.projectId ? { ownerProjectId: target.projectId } : {};
    const commandPayload = original?.payload ?? { ...payload, ...ownerProjectId };
    const storageKey = context && target ? assetTaskRecoveryKey(context.user.id, scope.workspaceId, scope.projectId ?? target.projectId ?? "", target.id, sessionEpoch) : undefined;
    let retained = original;
    if (isRecoverableAssetAction(action)) {
      if (!storageKey || !target || version === undefined || !Number.isSafeInteger(version) || version < 1) {
        setError(new Error(generationText("sourceMissing"))); return;
      }
      retained ??= { action, payload: commandPayload, targetId: target.id, projectId: target.projectId, version, idempotencyKey: `asset-action:${crypto.randomUUID()}` };
      if (!rememberPendingAssetSubmission(storageKey, retained)) {
        setError(new Error(generationText("recoveryStorageUnavailable"))); return;
      }
      setPendingRecovery({ key: storageKey, operation: retained });
    }
    operationLatch.current = true; setActiveAction(action); setError(undefined); setSourceError(undefined); setConflict(undefined); setMessage("");
    try {
      const result = await api.assetAction(scope, { action: action as never, targetId: retained?.targetId ?? target?.id, version: retained?.version ?? version, payload: commandPayload, ...(retained ? { idempotencyKey: retained.idempotencyKey } : {}) });
      if (retained && (action === "adoptGenerationOutput" ? result.asset?.id !== retained.targetId : !result.task?.taskId)) throw new Error(generationText("uncertain"));
      let receiptSaved = true;
      if (result.task) {
        const receipt = { ...result.task, projectId: result.task.projectId ?? scope.projectId ?? retained?.projectId ?? target?.projectId };
        setTask(receipt); setTaskOwnerId(retained?.targetId ?? target?.id);
        if (retained && storageKey) receiptSaved = rememberAssetTask(storageKey, receipt);
      }
      if (retained && storageKey) {
        if (receiptSaved) { const cleared = clearPendingAssetSubmission(storageKey); setPendingRecovery({ key: storageKey }); setTaskStorageUnavailable(!cleared); }
        else { setTaskStorageUnavailable(true); setMessage(generationText("receiptStorageUnavailable")); }
      }
      if (result.asset && ["createAsset", "updateAsset", "createVersion", "saveRights", "restoreVersion", "setPrimaryVersion", "adoptGenerationOutput"].includes(action)) { if (context) clearProductionDraft(productionDraftSlot({ actorId: context.user.id, workspaceId: context.workspace.id, projectId: scope.projectId ?? result.asset.projectId ?? null }, "asset", result.asset.id)); setDraftReset((value) => value + 1); setDraftBlocked(false); }
      if (result.asset) {
        const saved = result.asset;
        if (exactDetail && saved.id === targetAssetId) setDetail({ key: detailKey, loading: false, asset: saved });
        setAssets((items) => items?.map((item) => item.id === saved.id ? saved : item));
      }
      if (result.batch && action !== "extractAssets") setBatch(result.batch); if (result.message && receiptSaved) setMessage(result.message);
      await load();
      return result;
    } catch (cause) {
      const rejected = cause instanceof AssetsStoryboardApiError && typeof cause.details === "object" && cause.details !== null
        && "submissionState" in cause.details && cause.details.submissionState === "rejected";
      const uncertain = !rejected && (!(cause instanceof AssetsStoryboardApiError) || cause.status === null || cause.status >= 500 || cause.status === 408);
      if (retained && storageKey && !uncertain) { clearPendingAssetSubmission(storageKey); setPendingRecovery({ key: storageKey }); }
      if (retained && cause instanceof AssetsStoryboardApiError && cause.code === "GENERATION_INSUFFICIENT_CREDITS") {
        setSourceError({ message: generationText(context?.permissions.includes("billing.manage") ? "insufficient" : "askOwner"), assetId: target?.id, insufficient: true });
      } else if (retained && !uncertain && (rejected || cause instanceof AssetsStoryboardApiError && /^(MODEL_|GENERATION_(NATIVE_|MODEL_|PROVIDER_))/.test(cause.code))) {
        models.recheck();
        setSourceError({ message: generationText("notSubmitted"), assetId: target?.id, requestId: errorRequestId(cause) });
      } else if (retained && uncertain) {
        setReceiptCheck(undefined);
      } else if (action === "extractAssets" || action === "createAsset") {
        const code = cause instanceof AssetsStoryboardApiError ? cause.code : "";
        const stale = code === "FROZEN_SCRIPT_VERSION_ID_INVALID" || code === "FROZEN_SCRIPT_VERSION_NOT_FOUND";
        const messages = { NO_EXTRACTABLE_ASSETS: "noAssets", NARRATION_REQUIRES_INTERPRETATION: "reading", SEMANTIC_EXTRACTION_MODEL_FAILED: "modelFailed", SEMANTIC_EXTRACTION_IN_PROGRESS: "inProgress", PERMISSION_DENIED: "manageDenied" } as const;
        setSourceError({ message: extractionText(stale ? "stale" : messages[code as keyof typeof messages] ?? "submitFailed"), requestId: errorRequestId(cause) });
        if (stale) source.reload();
      } else if (isForbidden(cause)) { setForbidden(cause as AssetsStoryboardApiError); setAssets([]); setContext(undefined); } else if (isConflict(cause)) setConflict(cause as AssetsStoryboardApiError); else setError(cause);
      return undefined;
    } finally { operationLatch.current = false; setActiveAction(undefined); }
  };
  const title = activeView === "library" ? [libraryText("title"), libraryText("intro")] : activeView === "editor" ? [libraryText("editorTitle"), libraryText("editorIntro")] : activeView === "extract" ? [extractionText("title"), extractionText("intro")] : viewTitles[activeView];
  const currentError = error ?? (exactDetail && detail?.key === detailKey ? detail.error : undefined);
  if ((!context || !assets) && !forbidden && !error && !(exactDetail && detail?.key === detailKey && detail.error)) return <LoadingState />;
  if (forbidden) return <main className="min-h-full bg-[#07080C] p-6"><StatePanel title="没有资产查看权限" detail={libraryText("viewDenied")} requestId={forbidden.requestId} tone="danger" /></main>;
  if (exactDetail && (!detail || detail.key !== detailKey || detail.loading && !detail.asset)) return <LoadingState />;
  if (exactDetail && detail?.error && !detail.asset) return <main className="min-h-full bg-[#07080C] p-6"><StatePanel title={generationText("detailFailed")} detail={errorMessage(detail.error)} requestId={errorRequestId(detail.error)} actionLabel={generationText("reloadDetail")} onAction={() => void load()} tone="danger" /></main>;
  if (!context || !assets) return <main className="min-h-full bg-[#07080C] p-6"><StatePanel title={libraryText("loadFail")} detail={undefined} requestId={errorRequestId(error)} actionLabel="重新加载" onAction={() => void load()} tone="danger" /></main>;
  const hasProjectContext = Boolean(scope.projectId);
  const pendingCapability = pendingSubmission && pendingSubmission.action !== "adoptGenerationOutput" ? assetImageCapabilities[pendingSubmission.action] : undefined;
  const actions = activeView === "library" && hasProjectContext && canManage
    ? <button type="button" className="xj-lib-primary" onClick={() => navigate("extract")}>{libraryText("extract")}</button>
    : activeView === "editor" ? <button type="button" className={button} onClick={() => navigate("library")}>{libraryText("returnLibrary")}</button> : null;
  const episodeTitle = episodeBindings?.find((entry) => String(entry.episode) === scope.episodeId)?.title;
  const scopeTitle = scope.episodeId && hasProjectContext
    ? [libraryText("episode", { number: scope.episodeId }), episodeTitle].filter(Boolean).join(" · ")
    : libraryText(hasProjectContext ? "currentProject" : "workspace");
  const openFromLibrary = (asset: AssetItem) => {
    rememberLibrary(browseKey, { query: activeQuery, offset: libraryOffset, focusAsset: asset.id });
    navigate("editor", asset.id);
  };
  return <WorkspaceShell context={context} title={title[0]} description={title[1]} eyebrow="M04 · ASSET CONTINUITY" libraryHeader={activeView === "library"} projectName={projectName ?? context.project?.name} actions={actions}>
    {protection.navigationBlocked ? <p role="status" className="mb-4 text-sm text-amber-200">{handoff("unsaved")}</p> : null}{draftBlocked ? <div className="mb-4">{discardConfirm ? <><p className="text-xs leading-6 text-amber-200">{handoff("discardConfirm")}</p><button type="button" className={button + " mt-3"} disabled={busy} onClick={() => { if (currentAsset) clearProductionDraft(productionDraftSlot({ actorId: context.user.id, workspaceId: context.workspace.id, projectId: scope.projectId ?? currentAsset.projectId ?? null }, "asset", currentAsset.id)); setDraftReset((value) => value + 1); setDraftBlocked(false); setDiscardConfirm(false); setConflict(undefined); }}>{handoff("discard")}</button><button type="button" className={button + " ml-2 mt-3"} disabled={busy} onClick={() => setDiscardConfirm(false)}>{handoff("keep")}</button></> : <button type="button" className={button} disabled={busy} onClick={() => setDiscardConfirm(true)}>{handoff("discard")}</button>}</div> : null}{message ? <StatePanel title={message} tone="success" /> : null}{currentError ? <StatePanel title={activeView === "library" ? libraryText("loadFail") : "操作失败"} detail={activeView === "library" ? undefined : errorMessage(currentError)} requestId={errorRequestId(currentError)} actionLabel="重试加载" onAction={() => void load()} tone="danger" /> : null}
    {pendingSubmission && !busy ? <section role="status" className="mb-4 rounded-xl border border-amber-400/30 p-4">
      <p className="text-sm leading-6 text-amber-200">{generationText(!pendingCapability ? "uncertain" : missingReceipt ? "receiptMissing" : receiptCheck?.id === pendingSubmission.idempotencyKey ? "receiptFailed" : "checkingReceipt")}</p>
      {pendingCapability ? <button type="button" className={button + " mt-3"} onClick={() => { setReceiptCheck(undefined); recheckReceipt((value) => value + 1); }}>{generationText("checkReceipt")}</button> : null}
      {(!pendingCapability || missingReceipt && models.supports(pendingCapability)) ? <button type="button" className={button + " ml-2 mt-3"} disabled={draftBlocked} onClick={() => void run(pendingSubmission.action, pendingSubmission.payload, { id: pendingSubmission.targetId, projectId: pendingSubmission.projectId }, pendingSubmission.version, pendingSubmission)}>{generationText(pendingCapability ? "retryOriginal" : "recoverSubmission")}</button> : null}
    </section> : null}
    {taskStorageUnavailable ? <p role="status" className="mb-4 text-xs text-amber-200">{handoff("storageUnavailable")}</p> : null}
    {sourceError && (activeView === "extract" || activeView === "library") ? <StatePanel title={extractionText("operationFailed")} detail={sourceError.message} requestId={sourceError.requestId} tone="danger" /> : null}
    {activeView === "extract" ? <AssetExtraction {...sourceProps} api={api} scope={scope} task={taskOwnerId ? undefined : task} onLibrary={() => navigate("library")} onOpenAsset={(id) => navigate("editor", id)} onRun={(scriptVersionId, types, interpretNarration) => run("extractAssets", { scriptVersionId, assetTypes: types, ...(interpretNarration ? { interpretNarration } : {}) })} /> : null}
    {activeView === "library" ? <AssetLibrary scopeTitle={scopeTitle} episode={Boolean(scope.episodeId)} project={hasProjectContext} manage={canManage}
      ownerProject={scope.projectId} assets={assets} total={libraryTotal} offset={libraryOffset} query={activeQuery}
      loading={libraryLoading} busy={busy} restoreFocus={browse.focusAsset} selected={selectedAssetIds}
      manual={hasProjectContext && canManage ? <ManualAssetCreate key={draftReset} {...sourceProps} onDirty={setDraftBlocked}
        onCreate={(payload) => void run("createAsset", payload, undefined, undefined)} /> : undefined}
      onApply={(query, offset) => {
        setActiveQuery(query); setLibraryOffset(offset); setSelectedAssetIds([]); setBatch(undefined);
        rememberLibrary(browseKey, { query, offset }); void load(query, false, offset);
      }}
      onSelect={(id) => setSelectedAssetIds((ids) => ids.includes(id) ? ids.filter((old) => old !== id) : [...ids, id])}
      onBatch={() => {
        batchNames.current = new Map(assets.map((asset) => [asset.id, asset.name]));
        const records = Object.fromEntries(assets.filter((asset) => selectedAssetIds.includes(asset.id) && asset.rights?.id)
          .map((asset) => [asset.id, { version: asset.version, rightsId: asset.rights?.id }]));
        void run("confirmAssets", { itemIds: selectedAssetIds, ...(Object.keys(records).length ? { records } : {}) }, undefined, undefined);
      }}
      onOpen={openFromLibrary} onRights={(asset) => setPermission({ asset })}
      batchReport={batch ? <BatchResultPanel hideOperationId receipt={{...batch, items: batch.items.map((item) => ({ ...item,
        name: item.name ?? batchNames.current.get(item.id) ?? libraryText("assetLabel"),
        message: item.message && /^[A-Z_]+$/.test(item.message) ? libraryText(["RIGHTS_SELECTION_CHANGED", "VERSION_CONFLICT"].includes(item.message) ? "conflict" : "proofError") : item.message }))}}
        busy={busy} onRetry={(itemIds) => void run("retryFailedItems", { itemIds, previousOperationId: batch.operationId }, undefined, undefined)} /> : undefined} /> : null}
    {permission ? <AssetPermissionDialog key={permission.asset.id} initial={permission.asset} seed={permission.seed} api={api} scope={scope}
      editable={canManage && !editingLocked && (!scope.projectId || scope.projectId === permission.asset.projectId)}
      onClose={() => setPermission(undefined)} onSaved={(updated) => {
        setPermission((current) => current ? { ...current, asset: updated } : undefined);
        if (activeView === "editor") {
          if (permission.seed) clearProductionDraft(productionDraftSlot({ actorId: context.user.id, workspaceId: scope.workspaceId, projectId: scope.projectId ?? updated.projectId ?? null }, "asset", updated.id));
          setDraftReset((n) => n+1);
        }
        void load();
      }} /> : null}
    {activeView === "market" ? <>
      <AssetFilters search={search} type={type} rights={rights} onSearch={setSearch} onType={setType} onRights={setRights}
        onApply={() => { const query = { search: search || undefined, assetType: type || undefined, rightsStatus: rights || undefined }; setActiveQuery(query); void load(query); }} />
      {assets.length === 0 ? <StatePanel title="当前范围没有资产" detail="没有符合当前筛选和授权范围的市场素材。" actionLabel="清除筛选"
        onAction={() => { setSearch(""); setType(""); setRights(""); setActiveQuery({}); void load({}); }} />
        : <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">{assets.map((asset) =>
          <AssetCard key={asset.id} asset={asset} selected={false} canManage={false} onSelect={() => undefined} onOpen={() => navigate("market-detail", asset.id)} />)}</section>}
      {nextToken ? <button type="button" className={button + " mt-4"} onClick={() => void load(activeQuery, true)}>加载更多</button> : null}
    </> : null}
    {activeView === "editor" || activeView === "market-detail" ? currentAsset ? <AssetEditor generation={generation} key={`${currentAsset.id}:${draftReset}`} draftScope={{ actorId: context.user.id, workspaceId: context.workspace.id, projectId: scope.projectId ?? currentAsset.projectId ?? null }} onDirty={setDraftBlocked} asset={currentAsset} ownerProject={!scope.projectId || currentAsset.projectId === scope.projectId} task={taskOwnerId === currentAsset.id ? task : undefined} canManage={canManage && !editingLocked} busy={busy || Boolean(exactDetail && detail?.loading)} conflict={conflict} marketDetail={activeView === "market-detail"} onReload={() => void load()} onSave={(payload, version) => void run("updateAsset", payload, currentAsset, version)} onAction={(action, payload, version) => void run(action, payload, currentAsset, version)} onRights={(seed) => setPermission({ asset: currentAsset, seed })} onDerivative={(next) => scope.projectId ? navigate(next, currentAsset.id) : setLocalNavigation({ source: view, target: next, assetId: currentAsset.id })} /> : <StatePanel title="未找到资产" detail="对象可能已删除、筛选不可见或当前工作区无权访问。" actionLabel="返回资产库" onAction={() => navigate("library")} /> : null}
    {activeView === "turnaround" || activeView === "expressions" ? <CharacterDerivative generation={generation} asset={currentAsset} ownerProject={!scope.projectId || currentAsset?.projectId === scope.projectId} mode={activeView} canManage={canManage && !editingLocked} busy={busy || Boolean(exactDetail && detail?.loading)} task={taskOwnerId === currentAsset?.id ? task : undefined} onGenerateReference={() => void run("generateReferenceImage", { sourceVersionId: currentAsset?.currentVersionId })} onRun={(payload) => void run(activeView === "turnaround" ? "generateTurnaround" : "generateExpressions", payload)} onAdopt={(generatedAssetId) => void run("adoptGenerationOutput", { generatedAssetId })} /> : null}
  </WorkspaceShell>;
}

export type { AssetActionResult };
