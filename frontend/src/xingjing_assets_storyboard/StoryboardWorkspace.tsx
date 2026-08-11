import { useCallback, useEffect, useState } from "react";

import { AssetsStoryboardApiError, createAssetsStoryboardApi } from "./api";
import {
  moveStableItem,
  type AssetsStoryboardPort,
  type BatchReceipt,
  type PromptTemplateInput,
  type PromptTemplateItem,
  type QualityIssue,
  type SessionContext,
  type ShotItem,
  type ShotVersion,
  type TaskReceipt,
  type WorkspaceProjectScope,
} from "./contracts";
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
  readonly onNavigate?: (view: StoryboardView, shotId?: string) => void;
}

const defaultApi = createAssetsStoryboardApi();
const viewTitles: Record<StoryboardView, [string, string]> = {
  prompts: [
    "提示词中心",
    "管理图片与视频提示词、负面词、变量和模型适配版本，并追踪镜头使用记录。",
  ],
  list: [
    "分镜列表",
    "镜头 ID 永久不变；镜号、顺序、景别、运镜、资产引用和时长可形成新版本。",
  ],
  batch: [
    "分镜批量编辑",
    "一次版本变更批量修改景别、时长、资产绑定、模型策略与成本档位。",
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
  return error instanceof Error ? error.message : "服务暂时不可用，请重试";
}
function forbidden(error: unknown): boolean {
  return apiError(error)?.status === 403;
}
function conflict(error: unknown): boolean {
  return apiError(error)?.status === 409;
}

function ShotRail({
  shot,
  selected,
  canManage,
  onSelect,
  onMove,
}: {
  readonly shot: ShotItem;
  readonly selected: boolean;
  readonly canManage: boolean;
  readonly onSelect?: () => void;
  readonly onMove?: (delta: -1 | 1) => void;
}) {
  return (
    <article
      className={`${panel} grid overflow-hidden md:grid-cols-[92px_minmax(0,1fr)]`}
    >
      <div className="relative border-b border-[#2A2E3A] bg-[#0d0f15] p-4 md:border-b-0 md:border-r">
        <span className="absolute left-0 top-0 h-full w-1 bg-[#8B5CF6]" />
        <p className="font-mono text-[11px] text-[#7E8494]">SHOT</p>
        <strong className="mt-1 block text-xl">{shot.shotNo}</strong>
        <code className="mt-3 block break-all text-[10px] leading-4 text-[#7E8494]">
          {shot.id}
        </code>
      </div>
      <div className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-medium">
              {shot.shotSize} · {shot.cameraMove}
            </p>
            <p className="mt-1 text-xs text-[#B8BECC]">
              {shot.durationMs} ms · v{shot.version} ·{" "}
              {shot.assetReferences.map((asset) => asset.name).join(" / ") ||
                "未绑定资产"}
            </p>
          </div>
          {onSelect ? (
            <input
              type="checkbox"
              aria-label={`选择镜头 ${shot.shotNo}`}
              checked={selected}
              disabled={!canManage}
              onChange={onSelect}
              className="size-4 accent-[#8B5CF6]"
            />
          ) : null}
        </div>
        {shot.dialogue ? (
          <p className="mt-3 text-sm leading-6 text-[#B8BECC]">
            {shot.dialogue}
          </p>
        ) : null}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-[#3A3F4E] px-2 py-1 text-[11px] text-[#B8BECC]">
            {shot.status}
          </span>
          <span
            className={
              shot.issueCount
                ? "rounded-full border border-[#F59E0B]/45 bg-[#F59E0B]/10 px-2 py-1 text-[11px] text-[#fcd34d]"
                : "rounded-full border border-[#22C55E]/40 bg-[#22C55E]/10 px-2 py-1 text-[11px] text-[#86efac]"
            }
          >
            {shot.issueCount} 个问题
          </span>
          {shot.estimatedCredit !== undefined ? (
            <span className="rounded-full border border-[#3A3F4E] px-2 py-1 text-[11px] text-[#B8BECC]">
              预计 {shot.estimatedCredit} 算力
            </span>
          ) : null}
          {onMove ? (
            <>
              <button
                type="button"
                className={button}
                aria-label={`上移镜头 ${shot.shotNo}`}
                disabled={!canManage}
                onClick={() => onMove(-1)}
              >
                上移
              </button>
              <button
                type="button"
                className={button}
                aria-label={`下移镜头 ${shot.shotNo}`}
                disabled={!canManage}
                onClick={() => onMove(1)}
              >
                下移
              </button>
            </>
          ) : null}
        </div>
      </div>
    </article>
  );
}

function ShotList({
  shots,
  selected,
  canManage,
  onSelect,
  onMove,
}: {
  readonly shots: readonly ShotItem[];
  readonly selected: readonly string[];
  readonly canManage: boolean;
  readonly onSelect?: (id: string) => void;
  readonly onMove?: (id: string, delta: -1 | 1) => void;
}) {
  return (
    <section className="space-y-3" aria-label="镜头列表">
      {shots.map((shot) => (
        <ShotRail
          key={shot.id}
          shot={shot}
          selected={selected.includes(shot.id)}
          canManage={canManage}
          onSelect={onSelect ? () => onSelect(shot.id) : undefined}
          onMove={onMove ? (delta) => onMove(shot.id, delta) : undefined}
        />
      ))}
    </section>
  );
}

function BatchEditor({
  shots,
  selected,
  canManage,
  busy,
  onSelect,
  onApply,
}: {
  readonly shots: readonly ShotItem[];
  readonly selected: readonly string[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onSelect: (id: string) => void;
  readonly onApply: (payload: Record<string, unknown>) => void;
}) {
  const [shotSize, setShotSize] = useState("");
  const [durationMs, setDurationMs] = useState("");
  const [cameraMove, setCameraMove] = useState("");
  const [dialogue, setDialogue] = useState("");
  const [prompt, setPrompt] = useState("");
  const [modelPolicy, setModelPolicy] = useState("");
  const [costTier, setCostTier] = useState("");
  const changes = {
    shotSize: shotSize || undefined,
    durationMs: durationMs ? Number(durationMs) : undefined,
    cameraMove: cameraMove || undefined,
    dialogue: dialogue || undefined,
    prompt: prompt || undefined,
    modelPolicy: modelPolicy || undefined,
    costTier: costTier || undefined,
  };
  const hasChanges = Object.values(changes).some(
    (value) => value !== undefined,
  );
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <ShotList
        shots={shots}
        selected={selected}
        canManage={canManage}
        onSelect={onSelect}
      />
      <aside className={`${panel} h-fit p-5 xl:sticky xl:top-4`}>
        <h2 className="font-semibold">批量变更</h2>
        <p className="mt-2 text-xs text-[#7E8494]">
          已选择 {selected.length} 个稳定镜头
          ID；资产引用批量绑定尚待服务端入口接入。
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
            时长（毫秒）
            <input
              type="number"
              min="1"
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
          <label className="text-xs text-[#B8BECC]">
            模型策略
            <input
              value={modelPolicy}
              onChange={(event) => setModelPolicy(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs text-[#B8BECC]">
            成本档位
            <input
              value={costTier}
              onChange={(event) => setCostTier(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
        </div>
        <button
          type="button"
          className={`${primaryButton} mt-5 w-full`}
          disabled={!canManage || busy || selected.length === 0 || !hasChanges}
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
  onRun,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly issues: readonly QualityIssue[];
  readonly onRun: () => void;
}) {
  const allIssues = issues.length
    ? issues
    : shots.flatMap((shot) => shot.qualityIssues);
  return (
    <section>
      <div
        className={`${panel} flex flex-wrap items-center justify-between gap-3 p-5`}
      >
        <div>
          <h2 className="font-semibold">质量门禁</h2>
          <p className="mt-1 text-sm text-[#B8BECC]">
            阻断项未清零前，服务端不会确认故事板。
          </p>
        </div>
        <button
          type="button"
          className={primaryButton}
          disabled={!canManage || busy}
          onClick={onRun}
        >
          {busy ? "检测中…" : "运行质量检测"}
        </button>
      </div>
      {allIssues.length === 0 ? (
        <StatePanel
          title="尚无质量检测结果"
          detail="运行检测后会按稳定镜头 ID 返回连续性、资产、节奏与合规问题。"
        />
      ) : (
        <ul className="mt-4 space-y-3">
          {allIssues.map((issue) => (
            <li key={issue.id} className={`${panel} p-4`}>
              <div className="flex justify-between gap-3">
                <strong>{issue.code}</strong>
                <span
                  className={
                    issue.severity === "blocking"
                      ? "text-[#EF4444]"
                      : issue.severity === "warning"
                        ? "text-[#F59E0B]"
                        : "text-[#3B82F6]"
                  }
                >
                  {issue.severity}
                </span>
              </div>
              <p className="mt-2 text-sm text-[#B8BECC]">{issue.message}</p>
              {issue.recoveryAction ? (
                <p className="mt-2 text-xs text-[#8B5CF6]">
                  恢复动作：{issue.recoveryAction}
                </p>
              ) : null}
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

function SmartView({
  episodeId: initialEpisodeId,
  shots,
  canManage,
  busy,
  onGenerate,
  onShotAction,
  selected,
  onSelect,
  onMove,
}: {
  readonly episodeId?: string;
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly onGenerate: (payload: Record<string, unknown>) => void;
  readonly onShotAction: (action: string, shot: ShotItem) => void;
  readonly selected: readonly string[];
  readonly onSelect: (id: string) => void;
  readonly onMove: (id: string, delta: -1 | 1) => void;
}) {
  const [scriptVersionId, setScriptVersionId] = useState("");
  const [assetSnapshotId, setAssetSnapshotId] = useState("");
  const [episodeId, setEpisodeId] = useState(initialEpisodeId ?? "");
  return (
    <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside className={`${panel} h-fit p-5`}>
        <h2 className="font-semibold">生成输入</h2>
        <div className="mt-4 grid gap-3">
          <label className="text-xs">
            目标剧集 ID
            <input
              value={episodeId}
              onChange={(event) => setEpisodeId(event.target.value)}
              placeholder="episode-id"
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs">
            冻结剧本版本 ID
            <input
              value={scriptVersionId}
              onChange={(event) => setScriptVersionId(event.target.value)}
              placeholder="script-id@1"
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
          <label className="text-xs">
            资产快照 ID
            <input
              value={assetSnapshotId}
              onChange={(event) => setAssetSnapshotId(event.target.value)}
              placeholder="已提取资产对应的冻结快照"
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            />
          </label>
        </div>
        <p className="mt-3 text-xs text-[#7E8494]">
          导演设定随冻结剧本版本读取；服务端会拒绝未冻结剧本、跨项目快照和空资产快照。
        </p>
        <button
          type="button"
          className={`${primaryButton} mt-5 w-full`}
          disabled={
            !canManage ||
            busy ||
            !episodeId.trim() ||
            !scriptVersionId ||
            !assetSnapshotId
          }
          onClick={() =>
            onGenerate({
              episodeId: episodeId.trim(),
              scriptVersionId,
              assetSnapshotId,
            })
          }
        >
          {busy ? "生成中…" : "从冻结剧本创建镜头草案"}
        </button>
        {shots.length ? (
          <div className="mt-4 flex gap-2">
            <button
              type="button"
              className={button}
              disabled={selected.length !== 1}
              onClick={() => {
                const current = shots.find((shot) => shot.id === selected[0]);
                if (current) onShotAction("splitShot", current);
              }}
            >
              拆分选中镜头
            </button>
            <button
              type="button"
              className={button}
              disabled={selected.length < 2}
              onClick={() => {
                const current = shots.find((shot) => shot.id === selected[0]);
                if (current) onShotAction("mergeShots", current);
              }}
            >
              合并选中镜头
            </button>
          </div>
        ) : null}
      </aside>
      <ShotList
        shots={shots}
        selected={selected}
        canManage={canManage}
        onSelect={onSelect}
        onMove={onMove}
      />
    </div>
  );
}

function StoryboardViewPanel({
  shots,
  canManage,
  busy,
  collectionVersion,
  onSelectCandidate,
  onGenerateMedia,
  onConfirm,
}: {
  readonly shots: readonly ShotItem[];
  readonly canManage: boolean;
  readonly busy: boolean;
  readonly collectionVersion?: number;
  readonly onSelectCandidate: (shot: ShotItem, candidateId: string) => void;
  readonly onGenerateMedia: (
    shot: ShotItem,
    mediaType: "image" | "video",
  ) => void;
  readonly onConfirm: (storyboardId: string) => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const storyboardId = shots[0]?.storyboardId ?? "";
  return (
    <>
      <section
        className={`${panel} mb-4 flex flex-col gap-3 p-4 md:flex-row md:items-end md:justify-between`}
      >
        <div className="text-sm text-[#B8BECC]">
          当前故事板 ID
          <code className="mt-2 block rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-[#F5F6FA] md:w-80">
            {storyboardId || "尚未创建"}
          </code>
        </div>
        <button
          type="button"
          className={primaryButton}
          disabled={
            !canManage || busy || shots.length === 0 || !storyboardId.trim()
          }
          onClick={() => setConfirm(true)}
        >
          冻结并确认故事板
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
                  <div className="grid h-full place-items-center font-mono text-xs text-[#7E8494]">
                    {shot.id}
                  </div>
                )}
                <span className="absolute left-3 top-3 rounded-full bg-black/75 px-3 py-1 text-xs">
                  镜头 {shot.shotNo}
                </span>
              </div>
              <div className="p-4">
                <code className="text-[10px] text-[#7E8494]">{shot.id}</code>
                <p className="mt-2 text-sm text-[#B8BECC]">
                  {shot.shotSize} · {shot.cameraMove} · {shot.durationMs} ms
                </p>
                <p className="mt-2 line-clamp-3 text-sm">
                  {shot.prompt || "尚无提示词"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="text-xs text-[#B8BECC]">
                    {shot.assetReferences
                      .map((asset) => asset.name)
                      .join(" / ") || "未绑定资产"}
                  </span>
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
                    生成：{shot.generationStatus ?? "not_started"}
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
                  <code className="mt-2 block truncate text-[10px] text-[#7E8494]">
                    任务 {shot.generationTaskId}
                  </code>
                ) : null}
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={button}
                    disabled={
                      !canManage ||
                      busy ||
                      shot.status === "frozen" ||
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
                      shot.status === "frozen" ||
                      !shot.prompt?.trim()
                    }
                    onClick={() => onGenerateMedia(shot, "video")}
                  >
                    生成候选视频
                  </button>
                </div>
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
                          {candidate.id}
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
        detail={`将冻结当前 ${shots.length} 个镜头及其资产、提示词和候选画面引用。后续修改会创建新版本。`}
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
  const [adaptersText, setAdaptersText] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [adapter, setAdapter] = useState("");
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
    setAdaptersText(item.modelAdapterVersions.join(", "));
    setValues(
      Object.fromEntries(
        item.variables.map((key) => [key, shot?.promptVariables?.[key] ?? ""]),
      ),
    );
    setAdapter(item.modelAdapterVersions[0] ?? "");
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
    modelAdapterVersions: adaptersText
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
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
      setAdaptersText("");
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
              模板、变量、负面词和模型适配版本均由服务端持久化并保留版本。
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
              setAdaptersText("");
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
          <label className="text-xs">
            模型适配版本（逗号分隔）
            <input
              value={adaptersText}
              onChange={(event) => setAdaptersText(event.target.value)}
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
        {selectedTemplate?.modelAdapterVersions.length ? (
          <label className="mt-3 block text-xs">
            模型适配版本
            <select
              value={adapter}
              onChange={(event) => setAdapter(event.target.value)}
              className="mt-1 w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2"
            >
              {selectedTemplate.modelAdapterVersions.map((item) => (
                <option key={item}>{item}</option>
              ))}
            </select>
          </label>
        ) : null}
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
                modelAdapterVersion: adapter || undefined,
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
}: StoryboardWorkspaceProps) {
  const [localNavigation, setLocalNavigation] = useState<{
    readonly source: StoryboardView;
    readonly target: StoryboardView;
  }>();
  const activeView =
    localNavigation?.source === view ? localNavigation.target : view;
  const [context, setContext] = useState<SessionContext>();
  const [shots, setShots] = useState<readonly ShotItem[]>();
  const [collectionVersion, setCollectionVersion] = useState<number>();
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>(shotId ? [shotId] : []);
  const [error, setError] = useState<unknown>();
  const [denied, setDenied] = useState<AssetsStoryboardApiError>();
  const [versionConflict, setVersionConflict] =
    useState<AssetsStoryboardApiError>();
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState<BatchReceipt>();
  const [task, setTask] = useState<TaskReceipt>();
  const [taskShotId, setTaskShotId] = useState<string>();
  const [issues, setIssues] = useState<readonly QualityIssue[]>([]);
  const [resultMessage, setResultMessage] = useState("");
  const load = useCallback(
    async (append = false) => {
      await Promise.resolve();
      setError(undefined);
      setDenied(undefined);
      try {
        const session = context ?? (await api.getSessionContext(scope));
        if (!session.permissions.includes("shot.view")) {
          setDenied(
            new AssetsStoryboardApiError(
              403,
              "PERMISSION_DENIED",
              "没有分镜查看权限",
            ),
          );
          setShots([]);
          return;
        }
        const page = await api.listShots(scope, {
          pageToken: append ? (nextToken ?? undefined) : undefined,
        });
        setContext(session);
        setShots((current) =>
          append && current ? [...current, ...page.items] : page.items,
        );
        setNextToken(page.nextToken);
        setCollectionVersion(page.collectionVersion);
      } catch (cause) {
        if (forbidden(cause)) {
          setDenied(cause as AssetsStoryboardApiError);
          setShots([]);
          setContext(undefined);
        } else setError(cause);
      }
    },
    [api, context, nextToken, scope],
  );
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [api, scope.workspaceId, scope.projectId, scope.episodeId, activeView]); // eslint-disable-line react-hooks/exhaustive-deps
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
  const canManage = context?.permissions.includes("shot.manage") ?? false;
  const run = async (
    action: string,
    payload: Record<string, unknown> = {},
    target?: ShotItem,
    version = target?.version,
  ) => {
    setBusy(true);
    setError(undefined);
    setVersionConflict(undefined);
    setResultMessage("");
    try {
      const result = await api.shotAction(scope, {
        action: action as never,
        targetId: target?.id,
        version,
        payload,
      });
      if (result.shot)
        setShots((items) =>
          items?.map((item) =>
            item.id === result.shot?.id ? result.shot : item,
          ),
        );
      if (result.shots?.length) setShots(result.shots);
      if (result.batch) setBatch(result.batch);
      if (result.task) {
        setTask(result.task);
        setTaskShotId(target?.id);
      }
      if (result.issues) setIssues(result.issues);
      if (result.message) setResultMessage(result.message);
      await load();
      return result;
    } catch (cause) {
      if (forbidden(cause)) {
        setDenied(cause as AssetsStoryboardApiError);
        setShots([]);
        setContext(undefined);
      } else if (conflict(cause))
        setVersionConflict(cause as AssetsStoryboardApiError);
      else setError(cause);
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  const toggle = (id: string) =>
    setSelected((items) =>
      items.includes(id) ? items.filter((item) => item !== id) : [...items, id],
    );
  const move = (id: string, delta: -1 | 1) => {
    if (!shots) return;
    const previous = shots;
    const next = moveStableItem(previous, id, delta);
    setShots(next);
    void run(
      "reorder",
      {
        orderedShotIds: next.map((shot) => shot.id),
        baseVersion: collectionVersion,
      },
      undefined,
      undefined,
    ).then((result) => {
      if (!result) setShots(previous);
    });
  };
  const navigate = (next: StoryboardView, id?: string) => {
    if (onNavigate) onNavigate(next, id);
    else setLocalNavigation({ source: view, target: next });
  };
  if (!context && !denied && !error) return <LoadingState />;
  if (denied)
    return (
      <main className="min-h-full bg-[#07080C] p-6">
        <StatePanel
          title="没有分镜查看权限"
          detail="服务端拒绝了当前工作区、项目或剧集的数据访问。请申请 shot.view 权限。"
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
          detail={message(error)}
          requestId={apiError(error)?.requestId}
          actionLabel="重新加载"
          onAction={() => void load()}
          tone="danger"
        />
      </main>
    );
  const title = viewTitles[activeView];
  const items = shots ?? [];
  const actions = (
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
      title={title[0]}
      description={title[1]}
      eyebrow="M05 · SHOT CONTINUITY"
      projectName={projectName ?? context.project?.name}
      actions={actions}
    >
      {versionConflict ? (
        <StatePanel
          title="版本冲突"
          detail="当前分镜集合已被其他成员更新。已保留本地选择，请重新读取服务端版本后再提交。"
          requestId={versionConflict.requestId}
          actionLabel="重新读取服务端版本"
          onAction={() => void load()}
          tone="warning"
        />
      ) : null}
      {error ? (
        <StatePanel
          title="操作失败"
          detail={message(error)}
          requestId={apiError(error)?.requestId}
          actionLabel="重新加载"
          onAction={() => void load()}
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
          title="当前剧集没有镜头"
          detail="可从智能分镜生成草案，或导入通过服务端预检的分镜表。"
          actionLabel="进入智能分镜"
          onAction={() => navigate("smart")}
        />
      ) : null}
      {activeView === "list" && items.length ? (
        <>
          <ShotList
            shots={items}
            selected={selected}
            canManage={canManage}
            onSelect={toggle}
            onMove={move}
          />
          {nextToken ? (
            <button
              type="button"
              className={`${button} mt-4`}
              onClick={() => void load(true)}
            >
              加载更多
            </button>
          ) : null}
        </>
      ) : null}
      {activeView === "batch" && items.length ? (
        <BatchEditor
          shots={items}
          selected={selected}
          canManage={canManage}
          busy={busy}
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
      {activeView === "quality" && items.length ? (
        <QualityView
          shots={items}
          canManage={canManage}
          busy={busy}
          issues={issues}
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
      {activeView === "smart" ? (
        <SmartView
          episodeId={scope.episodeId}
          shots={items}
          canManage={canManage}
          busy={busy}
          selected={selected}
          onSelect={toggle}
          onMove={move}
          onGenerate={(payload) =>
            void run("generateShots", payload, undefined, undefined)
          }
          onShotAction={(action, shot) =>
            void run(action, { selectedShotIds: selected }, shot)
          }
        />
      ) : null}
      {activeView === "storyboard" && items.length ? (
        <StoryboardViewPanel
          shots={items}
          canManage={canManage}
          busy={busy}
          collectionVersion={collectionVersion}
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
                shotIds: items.map((shot) => shot.id),
              },
              undefined,
              undefined,
            )
          }
        />
      ) : null}
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
