import { useCallback, useEffect, useState } from "react";

import { AssetsStoryboardApiError, createAssetsStoryboardApi } from "./api";
import type {
  AssetActionResult,
  AssetItem,
  AssetType,
  AssetsStoryboardPort,
  BatchReceipt,
  RightsEvidenceUpload,
  RightsStatus,
  SessionContext,
  TaskReceipt,
  WorkspaceProjectScope,
} from "./contracts";
import { BatchResultPanel, ConfirmDialog, LoadingState, StatePanel, TaskSummary, WorkspaceShell, button, panel, primaryButton } from "./ui";

export type AssetView = "extract" | "library" | "editor" | "turnaround" | "expressions" | "market" | "market-detail";

export interface AssetsWorkspaceProps {
  readonly scope: WorkspaceProjectScope;
  readonly view?: AssetView;
  readonly assetId?: string;
  readonly projectName?: string;
  readonly api?: AssetsStoryboardPort;
  readonly onNavigate?: (view: AssetView, assetId?: string) => void;
}

const defaultApi = createAssetsStoryboardApi();
const assetTypeLabels: Record<AssetType, string> = { character: "角色", scene: "场景", prop: "道具", costume: "服装", voice: "声音" };
const rightsLabels: Record<RightsStatus, string> = { missing: "缺少授权", pending: "待核验", verified: "已授权", expired: "已过期", blocked: "已阻断" };
const viewTitles: Record<AssetView, [string, string]> = {
  extract: ["资产提取", "从冻结剧本提取角色、场景、道具、服装与声音主体，并以任务回执追踪结果。"],
  library: ["主体资产库", "按稳定资产 ID 维护项目与工作区复用对象，版本、授权和镜头引用在同一视图闭环。"],
  editor: ["资产编辑与版本", "编辑主体设定、权利记录和主版本；所有保存都携带当前服务端版本。"],
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

function AssetExtraction({ canManage, onRun, task, batch, busy }: { readonly canManage: boolean; readonly onRun: (scriptVersionId: string, types: AssetType[]) => void; readonly task?: TaskReceipt; readonly batch?: BatchReceipt; readonly busy: boolean }) {
  const [scriptVersionId, setScriptVersionId] = useState("");
  const [types, setTypes] = useState<AssetType[]>(["character", "scene", "prop", "costume", "voice"]);
  return <section className={`${panel} max-w-4xl p-6`}>
    <h2 className="text-lg font-semibold">提取输入</h2><p className="mt-2 text-sm text-[#B8BECC]">只接受已冻结剧本版本；角色与场景会从 M03 的已标注结构创建为真实资产，无法可靠推导的类别会明确跳过。</p>
    <label className="mt-5 block text-sm">冻结剧本版本 ID<input value={scriptVersionId} onChange={(event) => setScriptVersionId(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2" /></label>
    <fieldset className="mt-5"><legend className="text-sm">提取范围</legend><div className="mt-3 flex flex-wrap gap-3">{Object.entries(assetTypeLabels).map(([value, label]) => <label key={value} className="rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm"><input type="checkbox" className="mr-2 accent-[#8B5CF6]" checked={types.includes(value as AssetType)} onChange={() => setTypes((current) => current.includes(value as AssetType) ? current.filter((item) => item !== value) : [...current, value as AssetType])} />{label}</label>)}</div></fieldset>
    <button type="button" className={`${primaryButton} mt-6`} disabled={!canManage || busy || !scriptVersionId.trim() || types.length === 0} onClick={() => onRun(scriptVersionId.trim(), types)}>{busy ? "提交中…" : "开始提取资产"}</button>
    {task ? <TaskSummary task={task} message="提取任务已进入队列" /> : null}{batch ? <BatchResultPanel receipt={batch} busy={busy} /> : null}
  </section>;
}

function ManualAssetCreate({ canManage, busy, onCreate }: { readonly canManage: boolean; readonly busy: boolean; readonly onCreate: (payload: Record<string, unknown>) => void }) {
  const [snapshot, setSnapshot] = useState(""); const [kind, setKind] = useState<AssetType>("character"); const [name, setName] = useState(""); const [description, setDescription] = useState("");
  return <details className={`${panel} mb-5 p-5`}><summary className="cursor-pointer font-semibold text-[#F5F6FA]">从冻结剧本创建可编辑资产</summary><p className="mt-3 text-sm text-[#B8BECC]">此入口创建真实持久化的资产及首个版本。请输入 M03 中冻结的“剧本 ID@版本号”；角色三视图和表情集可在资产详情中提交到 M06 生成队列。</p><div className="mt-4 grid gap-3 md:grid-cols-2"><label className="text-sm text-[#B8BECC]">冻结剧本版本 ID<input value={snapshot} onChange={(event) => setSnapshot(event.target.value)} placeholder="script-...@1" className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3" /></label><label className="text-sm text-[#B8BECC]">资产类型<select value={kind} onChange={(event) => setKind(event.target.value as AssetType)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3">{Object.entries(assetTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="text-sm text-[#B8BECC]">资产名称<input value={name} onChange={(event) => setName(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3" /></label><label className="text-sm text-[#B8BECC]">资产说明<input value={description} onChange={(event) => setDescription(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3" /></label></div><button type="button" disabled={!canManage || busy || !snapshot.trim() || !name.trim()} onClick={() => onCreate({ frozenSnapshotId: snapshot.trim(), kind, name: name.trim(), metadata: { description } })} className={`${primaryButton} mt-4 disabled:opacity-50`}>{busy ? "正在创建…" : "创建资产"}</button></details>;
}

function MetadataEditor({ asset, values, onChange }: { readonly asset: AssetItem; readonly values: Record<string, string>; readonly onChange: (key: string, value: string) => void }) {
  const configs: Record<AssetType, Array<[string, string]>> = {
    character: [["appearance", "角色外观"], ["personality", "角色性格"], ["voiceAssetId", "绑定声音资产 ID"]],
    scene: [["environment", "场景环境"], ["lighting", "场景光线"], ["timeOfDay", "场景时段"]],
    prop: [["purpose", "道具用途"], ["material", "道具材质"], ["continuity", "连续性要求"]],
    costume: [["setting", "服装设定"], ["characterIds", "适用角色 ID"], ["continuity", "换装连续性"]],
    voice: [["timbre", "声音音色"], ["language", "声音语言"], ["emotion", "情绪范围"]],
  };
  return <div className="grid gap-4 md:grid-cols-2">{configs[asset.type].map(([key, label]) => <label key={key} className="text-sm text-[#B8BECC]">{label}<input value={values[key] ?? ""} onChange={(event) => onChange(key, event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label>)}</div>;
}

interface ConfirmState { readonly kind: "primary" | "restore" | "rights"; readonly id?: string; readonly label: string }

interface RightsDraft {
  readonly holder: string;
  readonly licenseScope: string;
  readonly proofObjectKey: string;
  readonly proofSha256: string;
  readonly validTo: string;
}

function RightsEditor({ value, editable, busy, onChange, onUpload, onSave }: {
  readonly value: RightsDraft;
  readonly editable: boolean;
  readonly busy: boolean;
  readonly onChange: (value: RightsDraft) => void;
  readonly onUpload: (file: File) => Promise<RightsEvidenceUpload>;
  readonly onSave: () => void;
}) {
  const [file, setFile] = useState<File>();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const upload = async () => {
    if (!file) return;
    setUploading(true); setUploadError("");
    try { await onUpload(file); setFile(undefined); }
    catch (reason) { setUploadError(errorMessage(reason)); }
    finally { setUploading(false); }
  };
  return <section className={`${panel} p-5`}><h2 className="font-semibold">授权记录</h2><p className="mt-1 text-xs text-[#7E8494]">上传后的文件会由服务端重新校验摘要；保存后进入待核验状态。</p><div className="mt-4 grid gap-3"><label className="text-xs text-[#B8BECC]">权利人<input disabled={!editable} value={value.holder} onChange={(event) => onChange({ ...value, holder: event.target.value })} className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 disabled:opacity-60" /></label><label className="text-xs text-[#B8BECC]">许可范围<select disabled={!editable} value={value.licenseScope} onChange={(event) => onChange({ ...value, licenseScope: event.target.value })} className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 disabled:opacity-60"><option value="owner_project_only">仅所属项目</option><option value="workspace">工作区复用</option></select></label><label className="text-xs text-[#B8BECC]">失效日期<input disabled={!editable} type="date" value={value.validTo} onChange={(event) => onChange({ ...value, validTo: event.target.value })} className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-2 py-2 disabled:opacity-60" /></label>{editable ? <><label className="text-xs text-[#B8BECC]">权利证明（PDF、JPG、PNG、WebP）<input type="file" accept="application/pdf,image/jpeg,image/png,image/webp" onChange={(event) => setFile(event.target.files?.[0])} className="mt-1 block w-full text-xs" /></label><button type="button" className={button} disabled={!file || uploading || busy} onClick={() => void upload()}>{uploading ? "正在上传…" : "上传并校验证明"}</button>{uploadError ? <StatePanel title="证明上传失败" detail={uploadError} tone="danger" /> : null}{value.proofObjectKey ? <p className="font-mono text-[11px] text-[#7E8494]">已校验对象：{value.proofObjectKey}</p> : null}<button type="button" className={button} disabled={!value.proofObjectKey || !value.proofSha256 || busy} onClick={onSave}>保存授权记录</button></> : null}</div></section>;
}

function MarketReusePanel({ asset, busy, onCreate }: {
  readonly asset: AssetItem;
  readonly busy: boolean;
  readonly onCreate: (payload: Record<string, unknown>) => void;
}) {
  const [projectName, setProjectName] = useState(`${asset.name} 副本`);
  const [commercialUse, setCommercialUse] = useState(false);
  const inheritableScopes = Array.isArray(asset.metadata.inheritableScopes)
    ? asset.metadata.inheritableScopes.filter((item): item is string => typeof item === "string")
    : [];
  const allowFork = asset.metadata.allowFork === true;
  const commercialAllowed = asset.metadata.commercialUse === true;
  const priceMinor = typeof asset.metadata.priceMinor === "number" ? asset.metadata.priceMinor : 0;
  return <section className={`${panel} p-5`}><h2 className="font-semibold">授权复用</h2><p className="mt-2 text-sm text-[#B8BECC]">创建操作走市场正式 Fork：锁定当前市场版本和源版本，并保存授权、分成与来源血缘。</p><dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><div><dt className="text-[#7E8494]">价格（最小计价单位）</dt><dd className="mt-1 font-mono text-[#F5F6FA]">{priceMinor}</dd></div><div><dt className="text-[#7E8494]">当前工作区使用记录</dt><dd className="mt-1 text-[#F5F6FA]">{asset.referenceCount} 次</dd></div><div><dt className="text-[#7E8494]">市场版本</dt><dd className="mt-1 font-mono text-[#F5F6FA]">v{asset.version}</dd></div><div><dt className="text-[#7E8494]">源版本</dt><dd className="mt-1 truncate font-mono text-[#F5F6FA]">{asset.currentVersionId}</dd></div></dl><label className="mt-4 block text-xs text-[#B8BECC]">新项目名称<input value={projectName} onChange={(event) => setProjectName(event.target.value)} className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2" /></label><label className="mt-3 block text-xs text-[#B8BECC]"><input type="checkbox" checked={commercialUse} disabled={!commercialAllowed} onChange={(event) => setCommercialUse(event.target.checked)} className="mr-2 accent-[#8B5CF6]" />用于商业项目{commercialAllowed ? "" : "（当前许可不允许）"}</label><p className="mt-3 text-xs text-[#7E8494]">可继承范围：{inheritableScopes.join("、") || "无"}</p><button type="button" className={`${primaryButton} mt-4 w-full`} disabled={busy || !allowFork || !projectName.trim()} onClick={() => onCreate({ projectName: projectName.trim(), intendedCommercialUse: commercialUse, requestedInheritableScopes: inheritableScopes, sourceVersionId: asset.currentVersionId })}>{busy ? "正在创建…" : allowFork ? "创建授权副本" : "当前素材禁止 Fork"}</button></section>;
}

function AssetEditor({ asset, canManage, busy, conflict, marketDetail, onSave, onAction, onReload, onUploadRightsEvidence, onDerivative }: {
  readonly asset: AssetItem;
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly conflict?: AssetsStoryboardApiError;
  readonly marketDetail: boolean;
  readonly onSave: (payload: Record<string, unknown>) => void;
  readonly onAction: (action: string, payload: Record<string, unknown>, confirm?: ConfirmState) => void;
  readonly onReload: () => void;
  readonly onUploadRightsEvidence: (file: File) => Promise<RightsEvidenceUpload>;
  readonly onDerivative: (view: "turnaround" | "expressions") => void;
}) {
  const [name, setName] = useState(asset.name);
  const [description, setDescription] = useState(asset.description ?? "");
  const [metadata, setMetadata] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(asset.metadata).map(([key, value]) => [key, typeof value === "string" ? value : ""])));
  const [rights, setRights] = useState<RightsDraft>({ holder: asset.rights?.holder ?? "", licenseScope: asset.rights?.licenseScope ?? "owner_project_only", proofObjectKey: asset.rights?.proofUploadId ?? "", proofSha256: "", validTo: asset.rights?.validTo ?? "" });
  const [confirm, setConfirm] = useState<ConfirmState>();
  const confirmAction = () => { if (!confirm) return; if (confirm.kind === "rights") onAction("saveRights", { ...rights }); else onAction(confirm.kind === "primary" ? "setPrimaryVersion" : "restoreVersion", { versionId: confirm.id }); setConfirm(undefined); };
  const canEdit = canManage && !marketDetail;
  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
    <section className={`${panel} p-6`}>
      <div className="flex items-start justify-between gap-4"><div><p className="text-xs text-[#8B5CF6]">{assetTypeLabels[asset.type]} · 稳定 ID</p><code className="mt-1 block text-xs text-[#7E8494]">{asset.id}</code></div><span className={`rounded-full border px-3 py-1 text-xs ${statusTone(asset.rightsStatus)}`}>{rightsLabels[asset.rightsStatus]}</span></div>
      <div className="mt-6 grid gap-4"><label className="text-sm text-[#B8BECC]">资产名称<input value={name} onChange={(event) => setName(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label><label className="text-sm text-[#B8BECC]">资产说明<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA]" /></label><MetadataEditor asset={asset} values={metadata} onChange={(key, value) => setMetadata((current) => ({ ...current, [key]: value }))} /></div>
      {marketDetail ? <StatePanel title="市场素材为只读版本" detail="该素材的授权、价格与复用规则由市场服务端维护。导入项目或创建 Fork 必须走 M13 的正式授权流程，不能在这里伪造资产副本。" tone="warning" /> : <button type="button" className={`${primaryButton} mt-6`} disabled={!canEdit || busy || !name.trim()} onClick={() => onSave({ name: name.trim(), description, metadata })}>{busy ? "保存中…" : "保存资产"}</button>}
      {!marketDetail && asset.type === "character" ? <div className="mt-4 flex flex-wrap gap-2"><button type="button" className={button} onClick={() => onDerivative("turnaround")}>生成角色三视图</button><button type="button" className={button} onClick={() => onDerivative("expressions")}>生成角色表情集</button></div> : null}
      {conflict ? <StatePanel title="版本冲突" detail="当前版本已被其他成员更新。你的输入仍保留，可以读取最新版本或另存新版本。" requestId={conflict.requestId} tone="warning" /> : null}
      {conflict ? <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={button} onClick={onReload}>重新读取服务端版本</button><button type="button" className={primaryButton} onClick={() => onAction("createVersion", { name: name.trim(), description, metadata })}>另存为新版本</button></div> : null}
    </section>
    <aside className="space-y-4">
      {marketDetail ? <MarketReusePanel asset={asset} busy={busy} onCreate={(payload) => onAction("licenseMarketAsset", payload)} /> : null}
      <section className={`${panel} p-5`}><h2 className="font-semibold">版本轨</h2><p className="mt-1 text-xs text-[#7E8494]">主版本与候选版本均绑定稳定资产 ID。</p><ol className="mt-4 space-y-3">{asset.versions.map((version) => <li key={version.id} className="rounded-[12px] border border-[#2A2E3A] bg-[#171922] p-3"><div className="flex items-center justify-between"><strong>v{version.versionNo}</strong><span className={`rounded-full border px-2 py-1 text-[11px] ${statusTone(version.status)}`}>{version.status}</span></div><code className="mt-2 block truncate text-[11px] text-[#7E8494]">{version.id}</code><div className="mt-3 flex gap-2"><button type="button" className={button} disabled={!canEdit || busy} aria-label={`设 v${version.versionNo} 为主版本`} onClick={() => setConfirm({ kind: "primary", id: version.id, label: `v${version.versionNo}` })}>设为主版本</button><button type="button" className={button} disabled={!canEdit || busy} onClick={() => setConfirm({ kind: "restore", id: version.id, label: `v${version.versionNo}` })}>恢复</button></div></li>)}</ol></section>
      <RightsEditor value={rights} editable={canEdit} busy={busy} onChange={setRights} onUpload={async (file) => { const uploaded = await onUploadRightsEvidence(file); setRights((current) => ({ ...current, proofObjectKey: uploaded.objectKey, proofSha256: uploaded.sha256 })); return uploaded; }} onSave={() => setConfirm({ kind: "rights", label: "授权记录" })} />
    </aside>
    <ConfirmDialog open={Boolean(confirm)} title={confirm?.kind === "primary" ? "确认设为主版本" : confirm?.kind === "restore" ? "确认恢复资产版本" : "确认保存授权记录"} detail={confirm?.kind === "primary" ? `设为主版本会影响 ${asset.referenceCount} 个镜头引用，请确认下游影响。` : confirm?.kind === "restore" ? "恢复操作会创建新的资产版本，不覆盖历史记录。" : "权利证明会进入下游合规与正式导出门禁。"} confirmLabel={confirm?.kind === "primary" ? "确认设为主版本" : confirm?.kind === "restore" ? `确认恢复 ${confirm.label}` : "确认保存授权"} busy={busy} onCancel={() => setConfirm(undefined)} onConfirm={confirmAction} />
  </div>;
}

function CharacterDerivative({ asset, mode, canManage, busy, onRun, onAdopt, task }: { readonly asset?: AssetItem; readonly mode: "turnaround" | "expressions"; readonly canManage: boolean; readonly busy: boolean; readonly onRun: (payload: Record<string, unknown>) => void; readonly onAdopt: (generatedAssetId: string) => void; readonly task?: TaskReceipt }) {
  const [values, setValues] = useState(mode === "turnaround" ? "正面,侧面,背面,半身,全身" : "喜悦,愤怒,悲伤,惊讶");
  if (!asset) return <StatePanel title="未找到角色资产" detail="请从资产库选择一个角色后再进入此页面。" />;
  return <section className={`${panel} max-w-4xl p-6`}><p className="text-xs text-[#8B5CF6]">角色稳定 ID</p><code className="text-xs text-[#7E8494]">{asset.id}</code><h2 className="mt-4 text-xl font-semibold">{asset.name}</h2><label className="mt-5 block text-sm">{mode === "turnaround" ? "需要生成的视图（逗号分隔）" : "需要生成的表情（逗号分隔）"}<textarea rows={3} value={values} onChange={(event) => setValues(event.target.value)} className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3" /></label><button type="button" className={`${primaryButton} mt-5`} disabled={!canManage || busy || !values.trim()} onClick={() => onRun({ items: values.split(",").map((item) => item.trim()).filter(Boolean), sourceVersionId: asset.currentVersionId })}>{busy ? "提交中…" : mode === "turnaround" ? "生成三视图版本" : "生成表情集版本"}</button>{task ? <TaskSummary task={task} busy={busy} onAdoptOutput={onAdopt} message={mode === "turnaround" ? "三视图任务已创建" : "表情集任务已创建"} /> : null}</section>;
}

export function AssetsWorkspace({ scope, view = "library", assetId, projectName, api = defaultApi, onNavigate }: AssetsWorkspaceProps) {
  const [localNavigation, setLocalNavigation] = useState<{ readonly source: AssetView; readonly target: AssetView }>();
  const activeView = localNavigation?.source === view ? localNavigation.target : view;
  const [context, setContext] = useState<SessionContext>();
  const [assets, setAssets] = useState<readonly AssetItem[]>();
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [activeQuery, setActiveQuery] = useState<{ search?: string; assetType?: AssetType; rightsStatus?: RightsStatus }>({});
  const [search, setSearch] = useState(""); const [type, setType] = useState<AssetType | "">(""); const [rights, setRights] = useState<RightsStatus | "">("");
  const [error, setError] = useState<unknown>(); const [forbidden, setForbidden] = useState<AssetsStoryboardApiError>(); const [conflict, setConflict] = useState<AssetsStoryboardApiError>();
  const [busy, setBusy] = useState(false); const [batch, setBatch] = useState<BatchReceipt>(); const [task, setTask] = useState<TaskReceipt>(); const [message, setMessage] = useState("");
  const load = useCallback(async (query = activeQuery, append = false) => {
    await Promise.resolve();
    setError(undefined); setForbidden(undefined);
    try {
      const session = context ?? await api.getSessionContext(scope);
      if (!session.permissions.includes("asset.view")) { setForbidden(new AssetsStoryboardApiError(403, "PERMISSION_DENIED", "没有资产查看权限")); setAssets([]); return; }
      const pageQuery = {
        ...query,
        pageToken: append ? nextToken ?? undefined : undefined,
        marketItemId: activeView === "market-detail" ? assetId : undefined,
      };
      const result = activeView === "market" || activeView === "market-detail" ? await api.listMarketAssets(scope, pageQuery) : await api.listAssets(scope, pageQuery);
      setContext(session); setAssets((current) => append && current ? [...current, ...result.items] : result.items); setNextToken(result.nextToken);
    } catch (cause) { if (isForbidden(cause)) { setForbidden(cause as AssetsStoryboardApiError); setAssets([]); setContext(undefined); } else setError(cause); }
  }, [activeQuery, activeView, api, assetId, context, nextToken, scope]);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(timer);
  }, [api, scope.workspaceId, scope.projectId, activeView]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!task || !["queued", "running", "retrying", "cancelling"].includes(task.status)) return undefined;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void api.getGenerationTask(scope, task, controller.signal)
        .then((current) => setTask(current))
        .catch((cause) => {
          if (!controller.signal.aborted) setError(cause);
        });
    }, 2_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [api, scope, task]);
  const canManage = context?.permissions.includes("asset.manage") ?? false;
  const currentAsset = assetId
    ? assets?.find((item) => item.id === assetId)
    : (activeView === "market-detail" || activeView === "turnaround" || activeView === "expressions" ? assets?.[0] : undefined);
  const navigate = (next: AssetView, id?: string) => { if (onNavigate) onNavigate(next, id); else setLocalNavigation({ source: view, target: next }); };
  const run = async (action: string, payload: Record<string, unknown> = {}, target = currentAsset, version = target?.version) => {
    setBusy(true); setError(undefined); setConflict(undefined); setMessage("");
    try {
      const ownerProjectId = !scope.projectId && target?.projectId ? { ownerProjectId: target.projectId } : {};
      const result = await api.assetAction(scope, { action: action as never, targetId: target?.id, version, payload: { ...payload, ...ownerProjectId } });
      if (result.asset) setAssets((items) => items?.map((item) => item.id === result.asset?.id ? result.asset : item));
      if (result.batch) setBatch(result.batch); if (result.task) setTask(result.task); if (result.message) setMessage(result.message);
      // 以服务端最终状态为准：写入成功后重新读取，避免把乐观本地状态误当成保存结果。
      await load();
      return result;
    } catch (cause) { if (isForbidden(cause)) { setForbidden(cause as AssetsStoryboardApiError); setAssets([]); setContext(undefined); } else if (isConflict(cause)) setConflict(cause as AssetsStoryboardApiError); else setError(cause); return undefined; } finally { setBusy(false); }
  };
  const title = viewTitles[activeView];
  if (!context && !forbidden && !error) return <LoadingState />;
  if (forbidden) return <main className="min-h-full bg-[#07080C] p-6"><StatePanel title="没有资产查看权限" detail="服务端拒绝了当前工作区或项目的数据访问。请联系管理员申请 asset.view 权限。" requestId={forbidden.requestId} tone="danger" /></main>;
  if (!context || error) return <main className="min-h-full bg-[#07080C] p-6"><StatePanel title="资产数据加载失败" detail={errorMessage(error)} requestId={errorRequestId(error)} actionLabel="重新加载" onAction={() => void load()} tone="danger" /></main>;
  const hasProjectContext = Boolean(scope.projectId);
  const actions = activeView === "library" && hasProjectContext ? <button type="button" className={button} onClick={() => navigate("extract")}>提取剧本资产</button> : null;
  return <WorkspaceShell context={context} title={title[0]} description={title[1]} eyebrow="M04 · ASSET CONTINUITY" projectName={projectName ?? context.project?.name} actions={actions}>
    {message ? <StatePanel title={message} tone="success" /> : null}{error ? <StatePanel title="操作失败" detail={errorMessage(error)} requestId={errorRequestId(error)} actionLabel="重试加载" onAction={() => void load()} tone="danger" /> : null}
    {activeView === "extract" ? <AssetExtraction canManage={canManage} busy={busy} task={task} batch={batch} onRun={(scriptVersionId, types) => void run("extractAssets", { scriptVersionId, assetTypes: types }, undefined, undefined)} /> : null}
    {activeView === "library" || activeView === "market" ? <>{activeView === "library" && hasProjectContext ? <ManualAssetCreate canManage={canManage} busy={busy} onCreate={(payload) => void run("createAsset", payload, undefined, undefined)} /> : null}<AssetFilters search={search} type={type} rights={rights} onSearch={setSearch} onType={setType} onRights={setRights} onApply={() => { const query = { search: search || undefined, assetType: type || undefined, rightsStatus: rights || undefined }; setActiveQuery(query); void load(query); }} />{assets?.length === 0 ? <StatePanel title="当前范围没有资产" detail={activeView === "market" ? "没有符合当前筛选和授权范围的市场素材。" : hasProjectContext ? "先从冻结剧本提取主体，或从冻结剧本创建可编辑资产。" : "当前工作区还没有可复用资产。请先在项目资产库中创建或提取资产。"} actionLabel={activeView === "market" ? "清除筛选" : hasProjectContext ? "开始资产提取" : undefined} onAction={() => { if (activeView === "market") { setSearch(""); setType(""); setRights(""); setActiveQuery({}); void load({}); } else if (hasProjectContext) navigate("extract"); }} /> : <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">{assets?.map((asset) => <AssetCard key={asset.id} asset={asset} selected={false} canManage={false} onSelect={() => undefined} onOpen={() => navigate(activeView === "market" ? "market-detail" : "editor", asset.id)} />)}</section>}{nextToken ? <button type="button" className={`${button} mt-4`} onClick={() => void load(activeQuery, true)}>加载更多</button> : null}{batch ? <BatchResultPanel receipt={batch} busy={busy} /> : null}</> : null}
    {activeView === "editor" || activeView === "market-detail" ? currentAsset ? <AssetEditor key={`${currentAsset.id}:${currentAsset.version}`} asset={currentAsset} canManage={canManage} busy={busy} conflict={conflict} marketDetail={activeView === "market-detail"} onReload={() => void load()} onSave={(payload) => void run("updateAsset", payload)} onAction={(action, payload) => void run(action, payload)} onUploadRightsEvidence={(file) => api.uploadRightsEvidence(scope, currentAsset, file)} onDerivative={(next) => scope.projectId ? navigate(next, currentAsset.id) : setLocalNavigation({ source: view, target: next })} /> : <StatePanel title="未找到资产" detail="对象可能已删除、筛选不可见或当前工作区无权访问。" actionLabel="返回资产库" onAction={() => navigate("library")} /> : null}
    {activeView === "turnaround" || activeView === "expressions" ? <CharacterDerivative asset={currentAsset} mode={activeView} canManage={canManage} busy={busy} task={task} onRun={(payload) => void run(activeView === "turnaround" ? "generateTurnaround" : "generateExpressions", payload)} onAdopt={(generatedAssetId) => void run("adoptGenerationOutput", { generatedAssetId })} /> : null}
  </WorkspaceShell>;
}

export type { AssetActionResult };
