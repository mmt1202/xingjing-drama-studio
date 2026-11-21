import { useSmartShotsTranslation } from "./smart-i18n";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, Clapperboard, Image as ImageIcon } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { ShotReviewList, ShotAssets } from "./ShotReviewList";
import { ShotDetailsEditor, useShotAssetChoices } from "./ShotDetailsEditor";
import { useShotReviewTranslation } from "./shot-review-i18n";
import { SHOTS_PER_PAGE, useShotCollection } from "./useShotCollection";
import "./asset-library.css";

import { AssetsStoryboardApiError, createAssetsStoryboardApi } from "./api";
import {
  type AssetItem,
  type AssetsStoryboardPort,
  type BatchReceipt,
  type PromptTemplateInput,
  type PromptTemplateItem,
  type QualityIssue,
  type QualityPolicyDescriptor,
  type ShotAction,
  type ShotItem,
  type ShotVersion,
  type TaskReceipt,
  type WorkspaceProjectScope,
} from "./contracts";
import { QualityPolicyNotice } from "./QualityPolicyNotice";
import { qualityErrorMessage, useStoryboardQualityTranslation } from "./quality-i18n";
import { useProductionHandoffTranslation } from "./handoff-i18n";
import { useDraftProtection } from "./useDraftProtection";
import { SmartStoryboardWorkspace, type SmartNavigation } from "./SmartStoryboardWorkspace";
import { StoryboardSourceNotice } from "./StoryboardSourceNotice";
import {
  BatchResultPanel,
  ConfirmDialog,
  LoadingState,
  StatePanel,
  TaskSummary,
  WorkspaceShell,
  button,
  panel,
  primaryButton,
} from "./ui";

export type StoryboardView =
  | "prompts"
  | "list"
  | "batch"
  | "quality"
  | "replace"
  | "export"
  | "import"
  | "smart"
  | "storyboard"
  | "compare";

export interface StoryboardWorkspaceProps {
  readonly scope: WorkspaceProjectScope;
  readonly view?: StoryboardView;
  readonly shotId?: string;
  readonly projectName?: string;
  readonly api?: AssetsStoryboardPort;
  readonly onNavigate?: (view: StoryboardView, shotId?: string, boardId?: string) => void;
  readonly initialBoardId?: string;
  readonly initialScriptVersionId?: string;
  readonly initialAssetSnapshotId?: string;
  readonly onOpenSmart?: SmartNavigation;
  readonly onSmartChange?: () => void;
}

const defaultApi = createAssetsStoryboardApi();
const viewTitles: Record<StoryboardView, [string, string]> = {
  prompts: [
    "提示词中心",
    "",
  ],
  list: ["", ""],
  batch: [
    "分镜批量编辑",
    "",
  ],
  quality: [
    "分镜质量检测",
    "检测缺角色、缺场景、台词过长、节奏断裂和合规风险，并提供逐项恢复动作。",
  ],
  replace: [
    "镜头替换",
    "用候选视频或修复版本替换当前镜头，同时保留原镜头 ID 和历史版本。",
  ],
  export: [
    "分镜表导出",
    "选择范围、版本和 XLSX / CSV / JSON 格式，创建可追踪导出任务。",
  ],
  import: [
    "分镜表导入",
    "上传文件、映射字段、服务端预检，并在确认后创建新的分镜版本。",
  ],
  smart: [
    "智能分镜",
    "依据冻结剧本、导演设定和资产快照生成草案，支持拆分、合并、重排和校验。",
  ],
  storyboard: [
    "故事板",
    "以接触表卡片组织资产引用、提示词、候选画面、生成状态、成本与质量问题。",
  ],
  compare: [
    "分镜版本对比",
    "并排比较 AI、人工调整、锁定和生成版本；恢复操作只创建新版本。",
  ],
};

function apiError(error: unknown): AssetsStoryboardApiError | undefined {
  return error instanceof AssetsStoryboardApiError ? error : undefined;
}
function message(error: unknown): string {
  const qualityMessage = qualityErrorMessage(apiError(error)?.code);
  if (qualityMessage) return qualityMessage;
  return error instanceof Error ? error.message : "服务暂时不可用，请重试";
}
function forbidden(error: unknown): boolean {
  return apiError(error)?.status === 403;
}
function conflict(error: unknown): boolean {
  return apiError(error)?.status === 409;
}

function BatchEditor({
  shots,
  assets,
  selected,
  canManage,
  busy,
  onSelect,
  onApply,
  order,
  pagination,
  assetsUnavailable,
}: {
  readonly shots: readonly ShotItem[];
  readonly assets: readonly AssetItem[];
  readonly selected: readonly string[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onSelect: (id: string) => void;
  readonly onApply: (payload: Record<string, unknown>) => void;
  readonly order: readonly string[];
  readonly pagination: ReactNode;
  readonly assetsUnavailable: boolean;
}) {
  const { t: review } = useShotReviewTranslation();
  const [shotSize, setShotSize] = useState("");
  const [durationMs, setDurationMs] = useState("");
  const [cameraMove, setCameraMove] = useState("");
  const [dialogue, setDialogue] = useState("");
  const [prompt, setPrompt] = useState("");
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const [assetSelectionChanged, setAssetSelectionChanged] = useState(false);
  const changes = {
    shotSize: shotSize || undefined,
    durationMs: durationMs ? Math.round(Number(durationMs) * 1000) : undefined,
    cameraMove: cameraMove || undefined,
    dialogue: dialogue || undefined,
    prompt: prompt || undefined,
    assetReferences: assetSelectionChanged
      ? assets
          .filter(
            (asset) =>
              assetIds.includes(asset.id) && Boolean(asset.currentVersionId),
          )
          .map((asset) => ({
            id: asset.id,
            versionId: asset.currentVersionId!,
            type: asset.type,
          }))
      : undefined,
  };
  const hasChanges = Object.values(changes).some(
    (value) => value !== undefined,
  );
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0"><ShotReviewList shots={shots} order={order} selected={selected} canManage={canManage} busy={busy} onSelect={onSelect} />{pagination}</div>
      <aside className={`${panel} h-fit p-5 xl:sticky xl:top-4`}>
        <h2 className="font-semibold">批量变更</h2>
        <p className="mt-2 text-xs text-[#7E8494]">
          {review("batchSelection", { n: selected.length })}
        </p>
        <div className="mt-4 grid gap-3">
          <label className="text-xs text-[#B8BECC]">
            景别
            <input
              value={shotSize}
              onChange={(event) => setShotSize(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs text-[#B8BECC]">
            运镜
            <input
              value={cameraMove}
              onChange={(event) => setCameraMove(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs text-[#B8BECC]">
            {review("duration")}
            <input
              type="number"
              min="0.001" step="0.001"
              value={durationMs}
              onChange={(event) => setDurationMs(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs text-[#B8BECC]">
            台词
            <textarea
              value={dialogue}
              onChange={(event) => setDialogue(event.target.value)}
              rows={2}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs text-[#B8BECC]">
            提示词
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={2}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <fieldset className="rounded-[10px] border border-[#3A3F4E] p-3">
            <legend className="px-1 text-xs text-[#B8BECC]">绑定项目资产</legend>
            <p className="mb-2 text-[11px] text-[#7E8494]">
              勾选后以当前版本替换所选镜头的资产引用；全部取消可清空绑定。
            </p>
            <div className="max-h-40 space-y-2 overflow-auto">
              {assets.filter((asset) => asset.currentVersionId).map((asset) => (
                <label key={asset.id} className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    aria-label={`绑定资产 ${asset.name}`}
                    checked={assetIds.includes(asset.id)}
                    onChange={() => {
                      setAssetSelectionChanged(true);
                      setAssetIds((current) =>
                        current.includes(asset.id)
                          ? current.filter((id) => id !== asset.id)
                          : [...current, asset.id],
                      );
                    }}
                  />
                  <span>{asset.name}</span>
                  <code className="ml-auto text-[10px] text-[#7E8494]">
                    {review(asset.type)}
                  </code>
                </label>
              ))}
              {assets.every((asset) => !asset.currentVersionId) ? (
                <p className="text-xs text-[#7E8494]">{review(assetsUnavailable ? "assetUnavailable" : "noAssetsFound")}</p>
              ) : null}
            </div>
          </fieldset>
        </div>
        <button
          type="button"
          className={`${primaryButton} mt-5 w-full`}
          disabled={!canManage || busy || selected.length === 0 || !hasChanges || (Boolean(durationMs) && (!/^\d+(?:\.\d{1,3})?$/.test(durationMs) || Number(durationMs) <= 0))}
          onClick={() => onApply({ itemIds: selected, changes })}
        >
          {busy ? "提交中…" : "应用批量编辑"}
        </button>
      </aside>
    </div>
  );
}

function QualityView({
  shots,
  canManage,
  busy,
  issues,
  policy,
  onRun,
  onEdit,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly issues: readonly QualityIssue[];
  readonly policy?: QualityPolicyDescriptor;
  readonly onRun: () => void;
  readonly onEdit: (shot: ShotItem) => void;
}) {
  const { t } = useStoryboardQualityTranslation();
  const { t: review } = useShotReviewTranslation();
  const savedIssues = shots.flatMap(shot => shot.qualityIssues.map(issue => ({ shot, issue })));
  const allIssues = savedIssues.length ? savedIssues : issues.map(issue => ({ shot: undefined, issue }));
  return (
    <section>
      <QualityPolicyNotice policy={policy} />
      <div
        className={`${panel} flex flex-wrap items-center justify-between gap-3 p-5`}
      >
        <div>
          <h2 className="font-semibold">{t("gateTitle")}</h2>
          <p className="mt-1 text-sm text-[#B8BECC]">
            {t("gateHelp")}
          </p>
        </div>
        <button
          type="button"
          className={primaryButton}
          disabled={!canManage || busy || policy?.configured === false}
          onClick={onRun}
        >
          {busy ? t("running") : t("run")}
        </button>
      </div>
      {allIssues.length === 0 ? (
        <StatePanel
          title={t("noIssuesTitle")}
          detail={t("noIssuesHelp")}
        />
      ) : (
        <ul className="mt-4 space-y-3">
          {allIssues.map(({ shot, issue }) => (
            <li key={issue.id} className={`${panel} p-4`}>
              <div className="flex justify-between gap-3">
                <strong>{shot && <span className="mr-2 text-violet-200">{review("shot", { no: shot.shotNo })}</span>}{review(issue.code, { defaultValue: review("issueFallback") })}</strong>
                <span
                  className={
                    issue.severity === "blocking"
                      ? "text-[#EF4444]"
                      : issue.severity === "warning"
                        ? "text-[#F59E0B]"
                        : "text-[#3B82F6]"
                  }
                >
                  {review(issue.severity)}
                </span>
              </div>
              {shot && <button type="button" className={button + " mt-3"} disabled={busy} onClick={() => onEdit(shot)}>
                {review(canManage && shot.status !== "frozen" ? "edit" : "review")}</button>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReplaceView({
  shots,
  canManage,
  busy,
  onReplace,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onReplace: (shot: ShotItem, candidateId: string) => void;
}) {
  const [shotId, setShotId] = useState(shots[0]?.id ?? "");
  const [candidateId, setCandidateId] = useState("");
  const current = shots.find((shot) => shot.id === shotId) ?? shots[0];
  return (
    <section className={`${panel} max-w-3xl p-6`}>
      <h2 className="font-semibold">替换输入</h2>
      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <label className="text-sm">
          稳定镜头 ID
          <select
            value={current?.id ?? ""}
            onChange={(event) => setShotId(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          >
            {shots.map((shot) => (
              <option key={shot.id} value={shot.id}>
                {shot.shotNo} · {shot.id}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          候选或修复版本 ID
          <input
            value={candidateId}
            onChange={(event) => setCandidateId(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          />
        </label>
      </div>
      <button
        type="button"
        className={`${primaryButton} mt-5`}
        disabled={!canManage || busy || !current || !candidateId.trim()}
        onClick={() => current && onReplace(current, candidateId.trim())}
      >
        {busy ? "替换中…" : "确认替换并创建版本"}
      </button>
    </section>
  );
}

function ExportView({
  canManage,
  busy,
  collectionVersion,
  storyboardId,
  onExport,
}: {
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly collectionVersion?: number;
  readonly storyboardId?: string;
  readonly onExport: (payload: Record<string, unknown>) => void;
}) {
  const [format, setFormat] = useState("xlsx");
  const [range, setRange] = useState("episode");
  const [version, setVersion] = useState(String(collectionVersion ?? ""));
  const [includePrompts, setIncludePrompts] = useState(true);
  return (
    <section className={`${panel} max-w-3xl p-6`}>
      <h2 className="font-semibold">正式分镜表导出</h2>
      <p className="mt-2 text-sm text-[#B8BECC]">
        导出会创建异步任务；当前服务端以故事板 ID
        作为导出对象，下载链接会由任务回执返回。
      </p>
      <div className="mt-5 grid gap-4 md:grid-cols-3">
        <div className="text-sm md:col-span-3">
          故事板 ID
          <code className="mt-2 block rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2">
            {storyboardId || "当前没有可导出的故事板"}
          </code>
        </div>
        <label className="text-sm">
          文件格式
          <select
            value={format}
            onChange={(event) => setFormat(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          >
            <option value="xlsx">XLSX</option>
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
        </label>
        <label className="text-sm">
          导出范围
          <select
            value={range}
            onChange={(event) => setRange(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          >
            <option value="episode">当前剧集</option>
            <option value="project">整个项目</option>
            <option value="selected">选中镜头</option>
          </select>
        </label>
        <label className="text-sm">
          分镜版本
          <input
            type="number"
            min="1"
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          />
        </label>
      </div>
      <label className="mt-4 block text-sm">
        <input
          type="checkbox"
          checked={includePrompts}
          onChange={(event) => setIncludePrompts(event.target.checked)}
          className="mr-2 accent-[#8B5CF6]"
        />
        包含提示词与生成状态
      </label>
      <button
        type="button"
        className={`${primaryButton} mt-5`}
        disabled={!canManage || busy || !version || !storyboardId}
        onClick={() =>
          onExport({
            storyboardId: storyboardId ?? "",
            format,
            range,
            version: Number(version),
            includePrompts,
          })
        }
      >
        {busy ? "创建中…" : "创建导出任务"}
      </button>
    </section>
  );
}

function ImportView({
  scope,
  api,
  canManage,
  busy,
  batch,
  onPreflight,
  onCommit,
}: {
  readonly scope: WorkspaceProjectScope;
  readonly api: AssetsStoryboardPort;
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly batch?: BatchReceipt;
  readonly onPreflight: (uploadId: string, fileName: string) => void;
  readonly onCommit: (uploadId: string, episodeId: string) => void;
}) {
  const [file, setFile] = useState<File>();
  const [uploadId, setUploadId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [episodeId, setEpisodeId] = useState(scope.episodeId ?? "");
  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setUploadError("");
    try {
      const receipt = await api.uploadImportFile(scope, file);
      setUploadId(receipt.uploadId);
      onPreflight(receipt.uploadId, receipt.fileName);
    } catch (cause) {
      setUploadError(message(cause));
    } finally {
      setUploading(false);
    }
  };
  const passed = Boolean(batch && batch.failed === 0 && batch.succeeded > 0);
  return (
    <section className={`${panel} max-w-4xl p-6`}>
      <h2 className="font-semibold">上传与服务端预检</h2>
      <p className="mt-2 text-sm text-[#B8BECC]">
        支持
        XLSX、CSV、JSON。文件先进入受控上传，再由服务端检查字段映射和资产引用。
      </p>
      <label className="mt-5 block text-sm">
        目标剧集 ID
        <input
          value={episodeId}
          onChange={(event) => setEpisodeId(event.target.value)}
          className="mt-2 block w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3"
          placeholder="episode-id"
        />
      </label>
      <label className="mt-5 block text-sm">
        选择分镜表文件
        <input
          type="file"
          accept=".xlsx,.csv,.json"
          className="mt-2 block w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3"
          onChange={(event) => setFile(event.target.files?.[0])}
        />
      </label>
      <button
        type="button"
        className={`${primaryButton} mt-4`}
        disabled={!canManage || busy || uploading || !file}
        onClick={() => void upload()}
      >
        {uploading ? "上传中…" : "上传并预检"}
      </button>
      {uploadError ? (
        <StatePanel title="上传失败" detail={uploadError} tone="danger" />
      ) : null}
      {batch ? (
        <BatchResultPanel receipt={batch} successTitle="导入预检完成" />
      ) : null}
      {passed ? (
        <>
          <StatePanel
            title="预检通过，可确认导入"
            detail="确认后会创建新的分镜版本，不会覆盖当前版本。"
            tone="success"
          />
          <button
            type="button"
            className={`${primaryButton} mt-4`}
            disabled={!episodeId.trim()}
            onClick={() => setConfirm(true)}
          >
            确认导入
          </button>
        </>
      ) : null}
      <ConfirmDialog
        open={confirm}
        title="确认导入分镜表"
        detail="导入会创建新版本并保留当前分镜；服务端仍会再次校验版本和权限。"
        confirmLabel="确认并创建新版本"
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          onCommit(uploadId, episodeId.trim());
        }}
      />
    </section>
  );
}

function StoryboardViewPanel({
  shots,
  canManage,
  busy,
  collectionVersion,
  total,
  onEdit,
  onSelectCandidate,
  onGenerateMedia,
  onConfirm,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly collectionVersion?: number;
  readonly total: number;
  readonly onEdit: (shot: ShotItem) => void;
  readonly onSelectCandidate: (shot: ShotItem, candidateId: string) => void;
  readonly onGenerateMedia: (
    shot: ShotItem,
    mediaType: "image" | "video",
  ) => void;
  readonly onConfirm: (storyboardId: string) => void;
}) {
  const { t } = useStoryboardQualityTranslation();
  const [confirm, setConfirm] = useState(false);
  const { t: review, i18n } = useShotReviewTranslation();
  const seconds = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 3 });
  const frozen = shots[0]?.status === "frozen";
  const storyboardId = shots[0]?.storyboardId ?? "";
  return (
    <>
      <section
        className={`${panel} mb-4 flex flex-col gap-3 p-4 md:flex-row md:items-end md:justify-between`}
      >
        <div className="text-sm text-[#B8BECC]">
          {review(frozen ? "frozenSummary" : "draftSummary")}
        </div>
        <button
          type="button"
          className={primaryButton}
          disabled={
            !canManage || busy || frozen || shots.length === 0 || !storyboardId.trim()
          }
          onClick={() => setConfirm(true)}
        >
          {frozen ? review("frozen") : review("freeze")}
        </button>
      </section>
      <section
        className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
        aria-label="故事板接触表"
      >
        {shots.map((shot) => {
          const selected =
            shot.candidates.find((candidate) => candidate.selected) ??
            shot.candidates[0];
          return (
            <article key={shot.id} className={`${panel} overflow-hidden`}>
              <div className="relative aspect-video bg-[#171922]">
                {selected?.thumbnailUrl ? (
                  <img
                    src={selected.thumbnailUrl}
                    alt={`镜头 ${shot.shotNo} 已选画面`}
                    className="h-full w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="grid h-full place-content-center justify-items-center gap-2 text-sm text-slate-400">
                    <ImageIcon size={24} aria-hidden="true"/>{review("blankFrame")}
                  </div>
                )}
                <span className="absolute left-3 top-3 rounded-full bg-black/75 px-3 py-1 text-xs">
                  镜头 {shot.shotNo}
                </span>
              </div>
              <div className="p-4">
                <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm text-slate-300">
                  {[shot.shotSize, shot.cameraMove, review("sec", { n: seconds.format(shot.durationMs / 1000) })].filter(Boolean).join(" · ")}
                </p><button type="button" className={button} disabled={busy} onClick={() => onEdit(shot)}>{review(canManage && !frozen ? "editNamed" : "review", { no: shot.shotNo })}</button></div>
                <p className="mt-2 line-clamp-3 text-sm">
                  {shot.prompt || "尚无提示词"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <ShotAssets shot={shot}/>
                  <span
                    className={
                      shot.issueCount
                        ? "text-xs text-[#F59E0B]"
                        : "text-xs text-[#22C55E]"
                    }
                  >
                    {shot.issueCount} 个问题
                  </span>
                  <span className="text-xs text-[#8B5CF6]">
                    {review("productionStatus")} · {review(shot.generationStatus === "failed" ? "generationFailed" : shot.generationStatus ?? "not_started")}
                  </span>
                  {shot.generationCurrency &&
                  shot.estimatedCostMinor !== undefined ? (
                    <span className="text-xs text-[#B8BECC]">
                      费用：预估 {shot.estimatedCostMinor}{" "}
                      {shot.generationCurrency}
                      {shot.actualCostMinor !== undefined
                        ? ` · 实际 ${shot.actualCostMinor} ${shot.generationCurrency}`
                        : ""}
                    </span>
                  ) : null}
                </div>
                {shot.generationFailure ? (
                  <p className="mt-2 rounded-lg border border-[#EF4444]/35 bg-[#2a1218] p-2 text-xs text-[#fca5a5]">
                    {shot.generationFailure}
                  </p>
                ) : null}
                {shot.generationTaskId ? (
                  <details className="mt-2 text-xs text-slate-400"><summary className="cursor-pointer">{review("taskDetails")}</summary><code className="break-all">{shot.generationTaskId}</code></details>
                ) : null}
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={button}
                    disabled={
                      !canManage ||
                      busy ||
                      (shot.generationSubmissionPending && shot.generationPendingMediaType !== "image") ||
                      !shot.prompt?.trim()
                    }
                    onClick={() => onGenerateMedia(shot, "image")}
                  >
                    生成候选图片
                  </button>
                  <button
                    type="button"
                    className={button}
                    disabled={
                      !canManage ||
                      busy ||
                      (shot.generationSubmissionPending && shot.generationPendingMediaType !== "video") ||
                      !shot.prompt?.trim()
                    }
                    onClick={() => onGenerateMedia(shot, "video")}
                  >
                    生成候选视频
                  </button>
                </div>
                {shot.generationSubmissionPending ? (
                  <p className="mt-2 text-xs text-[#B8BECC]">{t("submissionPending")}</p>
                ) : null}
                {shot.candidates.length > 1 ? (
                  <fieldset className="mt-4">
                    <legend className="text-xs text-[#7E8494]">候选画面</legend>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {shot.candidates.map((candidate) => (
                        <button
                          key={candidate.id}
                          type="button"
                          className={
                            candidate.selected ? primaryButton : button
                          }
                          disabled={!canManage || busy}
                          aria-pressed={candidate.selected}
                          onClick={() => onSelectCandidate(shot, candidate.id)}
                        >
                          {review("candidate", { n: candidate.version })}
                        </button>
                      ))}
                    </div>
                  </fieldset>
                ) : null}
              </div>
            </article>
          );
        })}
      </section>
      <ConfirmDialog
        open={confirm}
        title="确认冻结故事板"
        detail={t("freezePlanHelp", { count: total })}
        confirmLabel={`确认冻结版本 ${collectionVersion ?? "—"}`}
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          onConfirm(storyboardId);
        }}
      />
    </>
  );
}

function CompareView({
  shots,
  canManage,
  busy,
  onRestore,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onRestore: (shot: ShotItem, version: ShotVersion) => void;
}) {
  const [shotId, setShotId] = useState(shots[0]?.id ?? "");
  const [confirm, setConfirm] = useState<{
    shot: ShotItem;
    version: ShotVersion;
  }>();
  const shot = shots.find((item) => item.id === shotId) ?? shots[0];
  const versions = shot?.versions ?? [];
  return (
    <section>
      <label className="block max-w-xl text-sm">
        稳定镜头 ID
        <select
          value={shot?.id ?? ""}
          onChange={(event) => setShotId(event.target.value)}
          className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
        >
          {shots.map((item) => (
            <option key={item.id} value={item.id}>
              {item.shotNo} · {item.id}
            </option>
          ))}
        </select>
      </label>
      {shot ? (
        <code className="mt-3 block text-xs text-[#7E8494]">{shot.id}</code>
      ) : null}
      {versions.length ? (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {versions.slice(0, 2).map((version, index) => (
            <article key={version.id} className={`${panel} p-5`}>
              <p className="text-xs text-[#8B5CF6]">
                {index ? "候选版本" : "基准版本"}
              </p>
              <h2 className="mt-1 text-xl font-semibold">
                v{version.versionNo}
              </h2>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-[#7E8494]">景别</dt>
                  <dd>{version.shotSize}</dd>
                </div>
                <div>
                  <dt className="text-[#7E8494]">运镜</dt>
                  <dd>{version.cameraMove}</dd>
                </div>
                <div>
                  <dt className="text-[#7E8494]">时长</dt>
                  <dd>{version.durationMs} ms</dd>
                </div>
                <div>
                  <dt className="text-[#7E8494]">状态</dt>
                  <dd>{version.status}</dd>
                </div>
              </dl>
              <button
                type="button"
                className={`${button} mt-5`}
                disabled={!canManage || busy}
                onClick={() => shot && setConfirm({ shot, version })}
              >
                恢复 v{version.versionNo}
              </button>
            </article>
          ))}
        </div>
      ) : (
        <StatePanel
          title="没有可对比版本"
          detail="至少需要一个服务端分镜版本。"
        />
      )}
      <ConfirmDialog
        open={Boolean(confirm)}
        title="确认恢复镜头版本"
        detail="恢复历史版本会创建新版本，稳定镜头 ID 与历史记录保持不变。"
        confirmLabel={`确认恢复 v${confirm?.version.versionNo ?? "—"}`}
        busy={busy}
        onCancel={() => setConfirm(undefined)}
        onConfirm={() => {
          if (confirm) onRestore(confirm.shot, confirm.version);
          setConfirm(undefined);
        }}
      />
    </section>
  );
}

function PromptView({
  scope,
  api,
  shots,
  canManage,
  busy,
  onSave,
}: {
  readonly scope: WorkspaceProjectScope;
  readonly api: AssetsStoryboardPort;
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onSave: (shot: ShotItem, payload: Record<string, unknown>) => void;
}) {
  const { t: storyboardText } = useSmartShotsTranslation();
  const [templates, setTemplates] = useState<readonly PromptTemplateItem[]>();
  const [loadError, setLoadError] = useState("");
  const [shotId, setShotId] = useState(shots[0]?.id ?? "");
  const shot = shots.find((item) => item.id === shotId) ?? shots[0];
  const [templateId, setTemplateId] = useState("");
  const selectedTemplate = templates?.find((item) => item.id === templateId);
  const [name, setName] = useState("");
  const [mediaType, setMediaType] =
    useState<PromptTemplateInput["mediaType"]>("both");
  const [templateText, setTemplateText] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [variablesText, setVariablesText] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);
  const refresh = useCallback(
    () =>
      api
        .listPromptTemplates(scope)
        .then(setTemplates)
        .catch((cause) => setLoadError(message(cause))),
    [api, scope],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const edit = (item: PromptTemplateItem) => {
    setTemplateId(item.id);
    setName(item.name);
    setMediaType(item.mediaType);
    setTemplateText(item.template);
    setNegativePrompt(item.negativePrompt);
    setVariablesText(item.variables.join(", "));
    setValues(
      Object.fromEntries(
        item.variables.map((key) => [key, shot?.promptVariables?.[key] ?? ""]),
      ),
    );
  };
  const input = (): PromptTemplateInput => ({
    name: name.trim(),
    mediaType,
    template: templateText.trim(),
    negativePrompt: negativePrompt.trim(),
    variables: variablesText
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    modelAdapterVersions: selectedTemplate?.modelAdapterVersions ?? [],
  });
  const saveTemplate = async () => {
    setSavingTemplate(true);
    setLoadError("");
    try {
      if (selectedTemplate)
        await api.updatePromptTemplate(
          scope,
          selectedTemplate.id,
          selectedTemplate.version,
          input(),
        );
      else await api.createPromptTemplate(scope, input());
      await refresh();
    } catch (cause) {
      setLoadError(message(cause));
    } finally {
      setSavingTemplate(false);
    }
  };
  const archiveTemplate = async () => {
    if (!selectedTemplate) return;
    setSavingTemplate(true);
    setLoadError("");
    try {
      await api.archivePromptTemplate(
        scope,
        selectedTemplate.id,
        selectedTemplate.version,
      );
      setArchiveConfirm(false);
      setTemplateId("");
      setName("");
      setTemplateText("");
      setNegativePrompt("");
      setVariablesText("");

      await refresh();
    } catch (cause) {
      setLoadError(message(cause));
    } finally {
      setSavingTemplate(false);
    }
  };
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <section className={`${panel} p-6`}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">提示词模板库</h2>
            <p className="mt-1 text-xs text-[#7E8494]">
              {storyboardText("templateHelp")}
            </p>
          </div>
          <button
            type="button"
            className={button}
            onClick={() => {
              setTemplateId("");
              setName("");
              setTemplateText("");
              setNegativePrompt("");
              setVariablesText("");

            }}
          >
            新建模板
          </button>
        </div>
        {loadError ? (
          <StatePanel
            title="提示词模板加载失败"
            detail={loadError}
            tone="danger"
            actionLabel="重试"
            onAction={() => void refresh()}
          />
        ) : null}
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          {templates?.map((item) => (
            <button
              type="button"
              key={item.id}
              onClick={() => edit(item)}
              className={`rounded-xl border p-4 text-left ${item.id === templateId ? "border-[#8B5CF6] bg-[#8B5CF6]/10" : "border-[#2A2E3A] bg-[#171922]"}`}
            >
              <strong>{item.name}</strong>
              <p className="mt-2 line-clamp-2 text-xs text-[#B8BECC]">
                {item.template}
              </p>
              <p className="mt-3 text-[11px] text-[#7E8494]">
                {item.mediaType} · v{item.version} · {item.usageCount}{" "}
                个镜头使用
              </p>
            </button>
          ))}
        </div>
        <div className="mt-6 grid gap-3">
          <label className="text-xs">
            模板名称
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs">
            媒体类型
            <select
              value={mediaType}
              onChange={(event) =>
                setMediaType(
                  event.target.value as PromptTemplateInput["mediaType"],
                )
              }
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            >
              <option value="image">图片</option>
              <option value="video">视频</option>
              <option value="both">图片与视频</option>
            </select>
          </label>
          <label className="text-xs">
            模板正文（变量使用 {"{{name}}"}）
            <textarea
              rows={5}
              value={templateText}
              onChange={(event) => setTemplateText(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3"
            />
          </label>
          <label className="text-xs">
            负面词
            <textarea
              rows={3}
              value={negativePrompt}
              onChange={(event) => setNegativePrompt(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3"
            />
          </label>
          <label className="text-xs">
            变量名（逗号分隔）
            <input
              value={variablesText}
              onChange={(event) => setVariablesText(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <button
            type="button"
            className={primaryButton}
            disabled={
              !canManage ||
              savingTemplate ||
              !name.trim() ||
              !templateText.trim()
            }
            onClick={() => void saveTemplate()}
          >
            {savingTemplate
              ? "保存中…"
              : selectedTemplate
                ? "保存新版本"
                : "创建模板"}
          </button>
          {selectedTemplate ? (
            <button
              type="button"
              className={button}
              disabled={!canManage || savingTemplate}
              onClick={() => setArchiveConfirm(true)}
            >
              归档模板
            </button>
          ) : null}
        </div>
        <ConfirmDialog
          open={archiveConfirm}
          title="归档提示词模板"
          detail="归档后不能再应用到新镜头；已经保存到镜头版本中的渲染结果和使用记录会保留。"
          confirmLabel="确认归档"
          busy={savingTemplate}
          onCancel={() => setArchiveConfirm(false)}
          onConfirm={() => void archiveTemplate()}
        />
      </section>
      <section className={`${panel} h-fit p-6`}>
        <h2 className="font-semibold">应用到镜头</h2>
        <label className="mt-5 block text-sm">
          稳定镜头 ID
          <select
            value={shot?.id ?? ""}
            onChange={(event) => setShotId(event.target.value)}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          >
            {shots.map((item) => (
              <option key={item.id} value={item.id}>
                {item.shotNo} · {item.id}
              </option>
            ))}
          </select>
        </label>
        <label className="mt-4 block text-sm">
          选择模板
          <select
            value={templateId}
            onChange={(event) => {
              const item = templates?.find(
                (candidate) => candidate.id === event.target.value,
              );
              if (item) edit(item);
            }}
            className="mt-2 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
          >
            <option value="">选择服务端模板</option>
            {templates?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · v{item.version}
              </option>
            ))}
          </select>
        </label>
        {selectedTemplate?.variables.map((key) => (
          <label key={key} className="mt-3 block text-xs">
            变量 {key}
            <input
              value={values[key] ?? ""}
              onChange={(event) =>
                setValues((current) => ({
                  ...current,
                  [key]: event.target.value,
                }))
              }
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
        ))}
        {shot && selectedTemplate ? (
          <button
            type="button"
            className={`${primaryButton} mt-5 w-full`}
            disabled={
              !canManage ||
              busy ||
              selectedTemplate.variables.some((key) => !values[key]?.trim())
            }
            onClick={() =>
              onSave(shot, {
                prompt: selectedTemplate.template,
                negativePrompt: selectedTemplate.negativePrompt,
                templateId: selectedTemplate.id,
                variables: values,
              })
            }
          >
            {busy ? "应用中…" : "渲染并保存镜头版本"}
          </button>
        ) : (
          <p className="mt-5 text-sm text-[#7E8494]">
            选择模板和镜头后，可由服务端校验变量并渲染提示词。
          </p>
        )}
        {shot?.prompt ? (
          <div className="mt-6 rounded-xl border border-[#2A2E3A] bg-[#171922] p-4">
            <p className="text-xs text-[#7E8494]">当前渲染结果</p>
            <p className="mt-2 whitespace-pre-wrap text-sm">{shot.prompt}</p>
            {shot.negativePrompt ? (
              <>
                <p className="mt-4 text-xs text-[#7E8494]">负面词</p>
                <p className="mt-2 text-sm">{shot.negativePrompt}</p>
              </>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}

export function StoryboardWorkspace({
  scope,
  view = "list",
  shotId,
  projectName,
  api = defaultApi,
  onNavigate,
  initialScriptVersionId,
  initialAssetSnapshotId,
  initialBoardId,
  onOpenSmart,
  onSmartChange,
}: StoryboardWorkspaceProps) {
  const [localNavigation, setLocalNavigation] = useState<{
    readonly source: StoryboardView;
    readonly target: StoryboardView;
  }>();
  const activeView =
    localNavigation?.source === view ? localNavigation.target : view;
  const collection = useShotCollection(api, scope, activeView, initialBoardId);
  const { context, page, order, total, boardId, load } = collection;
  const shots = page?.items;
  const collectionVersion = page?.collectionVersion;
  const { t: review } = useShotReviewTranslation();
  const [editor, setEditor] = useState<ShotItem>();
  const [revisionDialog, setRevisionDialog] = useState(false);
  const [notice, setNotice] = useState("");
  const listTop = useRef<HTMLDivElement>(null), scrollAfterRead = useRef(false);
  const assetChoices = useShotAssetChoices(api, scope, activeView === "batch");
  const assets = assetChoices?.assets ?? [];
  const board = page?.shotList?.boards.find(entry => entry.id === boardId);
  const frozen = (board?.status ?? shots?.[0]?.status) === "frozen";
  const [selected, setSelected] = useState<string[]>(shotId ? [shotId] : []);
  const [actionError, setError] = useState<unknown>();
  const error = actionError ?? collection.error;
  const denied = forbidden(error) ? apiError(error) : undefined;
  const [versionConflict, setVersionConflict] =
    useState<AssetsStoryboardApiError>();
  const [busy, setBusy] = useState(false);
  const { t: handoff } = useProductionHandoffTranslation();
  const { t: storyboardText } = useSmartShotsTranslation();
  const protection = useDraftProtection(busy);
  const [batch, setBatch] = useState<BatchReceipt>();
  const [task, setTask] = useState<TaskReceipt>();
  const [taskShotId, setTaskShotId] = useState<string>();
  const [issues, setIssues] = useState<readonly QualityIssue[]>([]);
  const qualityPolicy = page?.qualityPolicy;
  const [resultMessage, setResultMessage] = useState("");
  useLayoutEffect(() => {
    if (page && scrollAfterRead.current) {
      scrollAfterRead.current = false;
      const start = listTop.current;
      start?.focus({ preventScroll: true });
      if (start && start.getBoundingClientRect().top < 0) start.scrollIntoView({ block: "start", behavior: "instant" });
    }
  }, [page]);
  useEffect(() => {
    if (task || !shots) return;
    const pending = shots.find(
      (shot) =>
        shot.generationTaskId &&
        ["queued", "running", "retrying", "cancelling"].includes(
          shot.generationStatus ?? "",
        ),
    );
    if (!pending?.generationTaskId) return;
    const timer = window.setTimeout(() => {
      setTaskShotId(pending.id);
      setTask({
        taskId: pending.generationTaskId!,
        projectId: scope.projectId,
        status: pending.generationStatus ?? "queued",
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [scope.projectId, shots, task]);
  useEffect(() => {
    if (
      !task ||
      !["queued", "running", "retrying", "cancelling"].includes(task.status)
    )
      return undefined;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void api
        .getGenerationTask(scope, task, controller.signal)
        .then((current) => setTask(current))
        .catch((cause) => {
          if (!controller.signal.aborted) setError(cause);
        });
    }, 2_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [api, scope, task]);
  const canManage = page?.shotList?.canEdit ?? context?.permissions.includes("shot.manage") ?? false;
  const running = useRef(false);
  const run = async (
    action: ShotAction,
    payload: Record<string, unknown> = {},
    target?: ShotItem,
    version = target?.version,
  ) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(undefined);
    setNotice("");
    setVersionConflict(undefined);
    setResultMessage("");
    try {
      const result = await api.shotAction(scope, {
        action,
        targetId: target?.id,
        version,
        payload,
      });
      if (result.batch && activeView === "batch") {
        const labels = new Map((result.shots ?? shots ?? []).map(item => [item.id, item.shotNo]));
        setBatch({ ...result.batch, items: result.batch.items.map(item => ({ ...item, name: item.name ??
          (labels.has(item.id) ? review("shot", { no: labels.get(item.id) }) : review("unknownShot")) })) });
      } else if (result.batch && activeView !== "list") setBatch(result.batch);
      if (result.task) {
        setTask(result.task);
        setTaskShotId(target?.id);
      }
      if (result.issues) setIssues(result.issues);
      if (result.message) setResultMessage(result.message);
      await load();
      return result;
    } catch (cause) {
      if (conflict(cause))
        setVersionConflict(cause as AssetsStoryboardApiError);
      else setError(cause);
      return undefined;
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  const toggle = (id: string) =>
    setSelected((items) =>
      items.includes(id) ? items.filter((item) => item !== id) : [...items, id],
    );
  const move = async (id: string, delta: -1 | 1) => {
    const from = order.indexOf(id), to = from + delta;
    if (!canManage || busy || frozen || from < 0 || to < 0 || to >= order.length) return;
    const next = [...order];
    [next[from], next[to]] = [next[to], next[from]];
    const result = await run("reorder", { orderedShotIds: next, baseVersion: collectionVersion });
    if (result && Math.floor(to / SHOTS_PER_PAGE) !== Math.floor(from / SHOTS_PER_PAGE)) {
      scrollAfterRead.current = true; collection.turnPage(Math.floor(to / SHOTS_PER_PAGE) * SHOTS_PER_PAGE);
    }
  };
  const navigate = (next: StoryboardView, id?: string) => {
    if (!protection.canNavigate()) return;
    if (onNavigate) onNavigate(next, id, boardId);
    else setLocalNavigation({ source: view, target: next });
  };
  if (activeView === "smart") return <SmartStoryboardWorkspace key={JSON.stringify([scope.workspaceId, scope.projectId, scope.episodeId])} api={api} scope={scope} scriptVersionId={initialScriptVersionId} assetSnapshotId={initialAssetSnapshotId} onOpen={onOpenSmart} onChange={onSmartChange} />;
  if (!context && !denied && !error) return <LoadingState />;
  if (denied)
    return (
      <main className="min-h-full bg-[#07080C] p-6">
        <StatePanel
          title={review("noAccess")}
          detail={review("viewAccess")}
          requestId={denied.requestId}
          tone="danger"
        />
      </main>
    );
  if (!context || (error && !shots))
    return (
      <main className="min-h-full bg-[#07080C] p-6">
        <StatePanel
          title="分镜数据加载失败"
          detail={review("actionFailed")}
          requestId={apiError(error)?.requestId}
          actionLabel="重新加载"
          onAction={retryLoad}
          tone="danger"
        />
      </main>
    );
  const title = viewTitles[activeView];
  const items = shots ?? [];
  const readBusy = busy || collection.loading;
  const workspaceView = ["list", "batch", "storyboard"].includes(activeView);
  const here = page?.shotList;
  const selectedHere = selected.filter(id => order.includes(id));
  const pagination = <Pagination label={review("pages")} unit="shots" pageSize={SHOTS_PER_PAGE} total={total} offset={collection.offset}
    disabled={readBusy} onOffsetChange={next => { scrollAfterRead.current = true; collection.turnPage(next); }} />;
  function chooseBoard(id: string) {
    setSelected([]); setBatch(undefined); setTask(undefined); setTaskShotId(undefined); setNotice(""); setIssues([]);
    setVersionConflict(undefined); setError(undefined); setResultMessage("");
    collection.chooseBoard(id);
    onNavigate?.(activeView, undefined, id);
  }
  function retryLoad() { setError(undefined); setVersionConflict(undefined); void load(); }
  async function createRevision() {
    if (!boardId || !canManage || !frozen || readBusy) return;
    const result = await run("reviseStoryboard", { storyboardId: boardId, baseVersion: collectionVersion });
    setRevisionDialog(false);
    if (result?.storyboardId) { chooseBoard(result.storyboardId); setNotice(review("revised")); onSmartChange?.(); }
  }
  const actions = workspaceView ? <div className="flex flex-wrap gap-2">
    {activeView !== "list" && <button className={button + " inline-flex items-center gap-2"} type="button" disabled={readBusy} onClick={() => navigate("list")}><ArrowLeft size={15}/>{review("back")}</button>}
    {activeView !== "batch" && !!total && <button className={button} type="button" disabled={readBusy} onClick={() => navigate("batch")}>{review("batch")}</button>}
    {!!total && <button className={button} type="button" disabled={readBusy} onClick={() => navigate("quality")}>{review("quality")}</button>}
    {activeView !== "storyboard" && !!total && <button type="button" className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-violet-200 px-4 py-2 text-sm font-semibold text-violet-950 hover:bg-violet-100 disabled:opacity-45"
      disabled={readBusy} onClick={() => navigate("storyboard")}>{review("next")}<ArrowRight size={15}/></button>}
  </div> : (
    <>
      <button type="button" className={button} onClick={() => navigate("list")}>
        镜头列表
      </button>
      <button
        type="button"
        className={button}
        onClick={() => navigate("batch")}
      >
        批量编辑
      </button>
      <button
        type="button"
        className={button}
        onClick={() => navigate("quality")}
      >
        质量检测
      </button>
      <button
        type="button"
        className={activeView === "storyboard" ? primaryButton : button}
        onClick={() => navigate("storyboard")}
      >
        故事板
      </button>
    </>
  );
  return (
    <WorkspaceShell
      context={context}
      libraryHeader={workspaceView}
      title={activeView === "list" ? review("title") : activeView === "storyboard" ? review("boardTitle") : title[0]}
      description={activeView === "list" ? review("intro") : activeView === "storyboard" ? review("boardHelp") : activeView === "prompts" ? storyboardText("promptHelp") : activeView === "batch" ? storyboardText("batchHelp") : title[1]}
      eyebrow="M05 · SHOT CONTINUITY"
      projectName={projectName ?? context.project?.name}
      actions={actions}
    >
      {protection.navigationBlocked ? <p role="status" className="mb-4 text-sm text-amber-200">{handoff("unsaved")}</p> : null}
      {workspaceView && total > 0 && <div ref={listTop} tabIndex={-1} className="mb-3 scroll-mt-4 rounded-xl border border-white/10 bg-[#11141d] px-4 py-3 outline-none focus-visible:ring-2 focus-visible:ring-violet-300">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2.5 text-sm">
            <Clapperboard aria-hidden="true" size={17} className="text-violet-300"/>
            <strong>{/^\d+$/.test(board?.episodeId ?? scope.episodeId ?? "") ? review("episode", { n: board?.episodeId ?? scope.episodeId }) : context.episode?.title || review("plan")}</strong>
            <span>{review("count", { n: total })}</span><span className="text-slate-400">{items.length === 1 ? review("single", { n: collection.offset + 1 }) : review("range", { from: collection.offset + 1, to: collection.offset + items.length })}</span>
            <span className="rounded-full bg-violet-300/10 px-2 py-1 text-xs text-violet-200">{review(frozen ? "frozen" : "draft")} · {review("version", { n: collectionVersion })}</span>
          </div>
          {here && here.boards.length > 1 && <label className="flex items-center gap-2 text-sm text-slate-400">{review("plan")}<select className={button} value={here.boardId ?? ""} disabled={readBusy}
            onChange={e => chooseBoard(e.target.value)}>{here.boards.map((b, i) => <option key={b.id} value={b.id}>{/^\d+$/.test(b.episodeId) ? review("episode", { n: b.episodeId }) + " · " : ""}{review("group", { n: i + 1 })} · {review(b.status === "frozen" ? "frozen" : "draft")} · {review("count", { n: b.count })}</option>)}</select></label>}
        </div>
        {here?.requestedBoardUnavailable && <p role="status" className="mt-2 text-sm text-amber-200">{review("missingBoard")}</p>}
        {!canManage && <p className="mt-2 text-sm text-amber-200">{review("readonly")}</p>}
        {frozen && canManage && <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-3 text-sm text-slate-300">
          {review("lockedHelp")}<button className={button} type="button" disabled={readBusy} onClick={() => setRevisionDialog(true)}>{review("revise")}</button></div>}
        <details className="mt-2 text-xs text-slate-400"><summary className="w-fit cursor-pointer">{review("sources")}</summary><StoryboardSourceNotice items={items} /></details>
      </div>}
      {!workspaceView && <StoryboardSourceNotice items={items}/>}
      {notice && <p role="status" className="mb-3 rounded-lg bg-emerald-300/10 px-4 py-2 text-sm text-emerald-200">{notice}</p>}
      {editor && <ShotDetailsEditor key={editor.id} shot={editor} scope={scope} api={api} writable={canManage} onClose={() => setEditor(undefined)}
        onSaved={no => { setEditor(undefined); setIssues([]); setNotice(review("saved", { no })); void load(); }} />}
      <ConfirmDialog open={revisionDialog} title={review("revise")} detail={review("reviseHelp")} confirmLabel={review("revise")} busy={busy}
        onCancel={() => setRevisionDialog(false)} onConfirm={() => void createRevision()} />
      {versionConflict ? (
        <StatePanel
          title="版本冲突"
          detail="当前分镜集合已被其他成员更新。已保留本地选择，请重新读取服务端版本后再提交。"
          requestId={versionConflict.requestId}
          actionLabel="重新读取服务端版本"
          onAction={retryLoad}
          tone="warning"
        />
      ) : null}
      {error ? (
        <StatePanel
          title="操作失败"
          detail={qualityErrorMessage(apiError(error)?.code) ?? review("actionFailed")}
          requestId={apiError(error)?.requestId}
          actionLabel="重新加载"
          onAction={retryLoad}
          tone="danger"
        />
      ) : null}
      {resultMessage && !task ? (
        <StatePanel title={resultMessage} tone="success" />
      ) : null}
      {task ? (
        <TaskSummary
          task={task}
          message={resultMessage || undefined}
          adoptLabel="采用为镜头候选"
          busy={busy}
          onAdoptOutput={
            taskShotId
              ? (generatedAssetId) => {
                  const shot = items.find((item) => item.id === taskShotId);
                  if (shot)
                    void run(
                      "adoptShotMedia",
                      { generatedAssetId, taskId: task.taskId },
                      shot,
                    );
                }
              : undefined
          }
        />
      ) : null}
      {items.length === 0 &&
      !["import", "export", "smart"].includes(activeView) ? (
        <StatePanel
          title={review("emptyTitle")}
          detail={review("emptyHelp")}
          actionLabel={review("emptyCta")}
          onAction={() => navigate("smart")}
        />
      ) : null}
      {activeView === "list" && items.length > 0 && <>
        <ShotReviewList shots={items} order={order} selected={selectedHere} canManage={canManage} busy={readBusy} onEdit={setEditor} onMove={(id, direction) => void move(id, direction)} />
        {pagination}
      </>}
      {activeView === "batch" && items.length ? (
        <BatchEditor
          shots={items}
          assets={assets}
          order={order}
          pagination={pagination}
          assetsUnavailable={Boolean(assetChoices?.error)}
          selected={selectedHere}
          canManage={canManage && !frozen}
          busy={readBusy}
          onSelect={toggle}
          onApply={(payload) =>
            void run(
              "batchUpdate",
              { ...payload, baseVersion: collectionVersion },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
      {activeView === "quality" && items.length === 0 ? (
        <QualityPolicyNotice policy={qualityPolicy} />
      ) : null}
      {activeView === "quality" && items.length ? (
        <QualityView
          shots={items}
          canManage={canManage}
          busy={busy}
          issues={issues}
          policy={qualityPolicy}
          onEdit={setEditor}
          onRun={() =>
            void run(
              "runQualityCheck",
              {
                shotIds: items.map((shot) => shot.id),
                baseVersion: collectionVersion,
              },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
      {activeView === "replace" ? (
        <ReplaceView
          shots={items}
          canManage={canManage}
          busy={busy}
          onReplace={(shot, candidateId) =>
            void run("replaceShot", { candidateId }, shot)
          }
        />
      ) : null}
      {activeView === "export" ? (
        <ExportView
          canManage={canManage}
          busy={busy}
          collectionVersion={collectionVersion}
          storyboardId={items[0]?.storyboardId}
          onExport={(payload) =>
            void run("createExport", payload, undefined, undefined)
          }
        />
      ) : null}
      {activeView === "import" ? (
        <ImportView
          scope={scope}
          api={api}
          canManage={canManage}
          busy={busy}
          batch={batch}
          onPreflight={(uploadId, fileName) =>
            void run(
              "preflightImport",
              { uploadId, fileName, baseVersion: collectionVersion },
              undefined,
              undefined,
            )
          }
          onCommit={(uploadId, episodeId) =>
            void run(
              "commitImport",
              { uploadId, episodeId, baseVersion: collectionVersion },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
      {activeView === "storyboard" && items.length ? (
        <StoryboardViewPanel
          shots={items}
          canManage={canManage}
          busy={busy}
          collectionVersion={collectionVersion}
          total={total}
          onEdit={setEditor}
          onSelectCandidate={(shot, candidateId) =>
            void run("selectStoryboardCandidate", { candidateId }, shot)
          }
          onGenerateMedia={(shot, mediaType) =>
            void run("generateShotMedia", { mediaType }, shot)
          }
          onConfirm={(storyboardId) =>
            void run(
              "confirmStoryboard",
              {
                storyboardId,
                baseVersion: collectionVersion,
                shotIds: order,
              },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
      {activeView === "storyboard" && pagination}
      {activeView === "compare" && items.length ? (
        <CompareView
          shots={items}
          canManage={canManage}
          busy={busy}
          onRestore={(shot, version) =>
            void run("restoreVersion", { versionId: version.id }, shot)
          }
        />
      ) : null}
      {activeView === "prompts" ? (
        <PromptView
          scope={scope}
          api={api}
          shots={items}
          canManage={canManage}
          busy={busy}
          onSave={(shot, payload) => void run("savePrompt", payload, shot)}
        />
      ) : null}
      {batch && activeView !== "import" ? (
        <BatchResultPanel
          receipt={batch}
          hideOperationId
          busy={busy}
          onRetry={(itemIds) =>
            void run(
              "retryFailedItems",
              {
                itemIds,
                previousOperationId: batch.operationId,
              },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
    </WorkspaceShell>
  );
}
