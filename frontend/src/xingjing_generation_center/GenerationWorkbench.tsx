import i18n from "@/i18n";
import { ProductionQueue, PRODUCTION_PAGE_SIZE, queueLocation } from "./ProductionQueue";
import { useQueueText } from "./queue-i18n";
import { Clapperboard, Film, ImagePlus, LoaderCircle, ClipboardList, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AuthenticatedVideo } from "@/components/shared/AuthenticatedVideo";
import { useAuthStore } from "@/stores/auth-store";
import { getTokenForSession } from "@/utils/auth";

import { GenerationSubmissionForm } from "./GenerationSubmissionForm";
import { repeatRequest } from "./submission-recovery";
import { useSubmissionTranslation } from "./submission-i18n";
import { GenerationCenterApiError, createGenerationCenterApi, type GenerationCenterApi } from "./api";
import { isProcessingTask, type ProductionAction, type GenerationCandidateSet, type GenerationTask, type GenerationTaskDetail, type GenerationTaskFilters, type GenerationWorkbenchView, type GenerationWorkspaceScope } from "./contracts";

const copy: Record<Exclude<GenerationWorkbenchView, `model-${string}`>, { title: string; eyebrow: string; description: string; icon: typeof Sparkles; capability: string; media: "image" | "video"; acceptsInput?: boolean; submit?: boolean }> = {
  center: { title: "生成中心", eyebrow: "GENERATION CONTROL ROOM", description: "", icon: Sparkles, capability: "generation", media: "image", submit: true },
  image: { title: "图片生成工作台", eyebrow: "STILL FRAME LAB", description: "围绕角色、场景与分镜首帧组织图片生成任务。", icon: ImagePlus, capability: "image_generation", media: "image", submit: true },
  "image-to-video": { title: "图生视频", eyebrow: "FRAME TO MOTION", description: "从故事板首帧、角色图或场景图追踪镜头视频任务。", icon: Film, capability: "image_to_video", media: "video", acceptsInput: true, submit: true },
  "text-to-video": { title: "文生视频", eyebrow: "SCRIPT TO MOTION", description: "按分镜文本、运镜与情绪观察视频候选生成。", icon: Clapperboard, capability: "text_to_video", media: "video", submit: true },
  video: { title: "视频生成工作台", eyebrow: "MOTION FINISHING", description: "集中查看视频生成、延展、去闪与高清处理任务。", icon: Film, capability: "video_generation", media: "video", acceptsInput: true, submit: true },
  tasks: { title: "任务中心", eyebrow: "PRODUCTION QUEUE", description: "查看当前项目的排队、运行、失败与取消结果。", icon: LoaderCircle, capability: "generation", media: "video" },
  "first-last-video": { title: "首尾帧视频", eyebrow: "KEYFRAME MOTION", description: "用首帧与尾帧约束镜头运动和收束画面。", icon: Film, capability: "first_last_video", media: "video", acceptsInput: true, submit: true },
  "image-edit": { title: "图片编辑", eyebrow: "IMAGE EDIT LAB", description: "提交受控图片编辑任务并保留输入资产关联。", icon: ImagePlus, capability: "image_edit", media: "image", acceptsInput: true, submit: true },
  inpaint: { title: "局部重绘", eyebrow: "INPAINT CONTROL", description: "以遮罩与原图资产为输入生成局部修复版本。", icon: ImagePlus, capability: "inpaint", media: "image", acceptsInput: true, submit: true },
  "reference-fusion": { title: "多图参考融合", eyebrow: "REFERENCE FUSION", description: "将多张角色或场景参考资产提交给模型融合。", icon: ImagePlus, capability: "reference_fusion", media: "image", acceptsInput: true, submit: true },
  "image-upscale": { title: "高清放大", eyebrow: "IMAGE UPSCALE", description: "提交已有图片资产的超分与细节修复。", icon: ImagePlus, capability: "image_upscale", media: "image", acceptsInput: true, submit: true },
  "video-candidates": { title: "视频候选对比", eyebrow: "CANDIDATE REVIEW", description: "从服务端确认的生成产物中并排比对候选版本。", icon: Film, capability: "video_candidates", media: "video", acceptsInput: true, submit: true },
  "video-extend": { title: "视频延展", eyebrow: "MOTION EXTEND", description: "以已有视频资产为输入延长镜头和环境运动。", icon: Film, capability: "video_extend", media: "video", acceptsInput: true, submit: true },
  "video-retry": { title: "视频重试策略", eyebrow: "RECOVERY CONTROL", description: "从失败任务复制输入后以新请求重新提交；不会覆盖旧任务。", icon: Film, capability: "video_retry", media: "video", acceptsInput: true, submit: true },
  "video-stabilize": { title: "视频去闪稳定", eyebrow: "FRAME STABILIZE", description: "对服务端已确认的视频资产提交去闪和稳定化任务。", icon: Film, capability: "video_stabilize", media: "video", acceptsInput: true, submit: true },
  "video-upscale": { title: "视频高清修复", eyebrow: "VIDEO RESTORATION", description: "对已有视频资产提交超分、补帧和修复任务。", icon: Film, capability: "video_upscale", media: "video", acceptsInput: true, submit: true },
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function artifactUrl(scope: GenerationWorkspaceScope, assetId: string): string {
  return `/api/v1/generation-assets/${encodeURIComponent(assetId)}/content?projectId=${encodeURIComponent(scope.projectId)}`;
}

function CandidateComparison({ scope, candidates, total, selecting, onSelectCandidate }: { readonly scope: GenerationWorkspaceScope; readonly candidates: readonly GenerationCandidateSet[]; readonly total: number; readonly selecting: boolean; readonly onSelectCandidate: (taskId: string, assetId: string, expectedVersion: number) => void }) {
  const { t } = useSubmissionTranslation();
  return <section className="border border-cyan-200/20 bg-cyan-200/[0.035] p-5"><div className="flex flex-wrap items-end justify-between gap-3"><div><p className="font-mono text-[11px] tracking-[.16em] text-cyan-200">SERVER-CONFIRMED CANDIDATES</p><h2 className="mt-1 text-lg font-semibold text-cyan-50">视频候选对比</h2><p className="mt-1 text-sm text-stone-400">{t("compareHelp")}</p></div><span className="text-sm text-stone-500">共 {total} 组候选</span></div>{candidates.length ? <div className="mt-4 grid gap-4 2xl:grid-cols-2">{candidates.map((candidate) => <article key={candidate.task.task_id} className="border border-white/10 bg-black/25 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-mono text-xs text-stone-200">{candidate.task.task_id}</p><p className="mt-1 text-xs text-stone-500">{candidate.task.request.prompt}</p></div><span className="text-xs text-stone-500">第 {candidate.task.attempt + 1} 次</span></div><div className="mt-3 grid gap-3 sm:grid-cols-2">{candidate.assets.map((asset) => <div key={asset.asset_id} className="border border-white/10 bg-stone-950/60 p-2"><AuthenticatedVideo workspaceId={scope.workspaceId} projectId={scope.projectId} source={artifactUrl(scope, asset.asset_id)} className="aspect-video w-full bg-black object-contain" /><p className="mt-2 break-all font-mono text-[10px] text-stone-300">{asset.asset_id}</p><p className="mt-1 text-[10px] text-stone-500">{formatBytes(asset.size_bytes)} · {asset.content_sha256.slice(0, 16)}…</p><button type="button" disabled={selecting || candidate.selectedCandidate?.selected_asset_id === asset.asset_id} onClick={() => onSelectCandidate(candidate.task.task_id, asset.asset_id, candidate.selectedCandidate?.version ?? 0)} className="mt-2 border border-cyan-200/30 px-2 py-1 text-[11px] text-cyan-100 disabled:opacity-50">{candidate.selectedCandidate?.selected_asset_id === asset.asset_id ? "已保留此版本" : "保留此版本"}</button></div>)}</div>{candidate.assets.length < 2 ? <p className="mt-3 text-xs text-stone-500">{t("oneCandidate")}</p> : null}</article>)}</div> : <p className="mt-4 text-sm text-stone-500">还没有可比较的已完成视频产物。</p>}</section>;
}

export interface GenerationWorkbenchProps {
  readonly scope: GenerationWorkspaceScope;
  readonly view?: GenerationWorkbenchView;
  readonly api?: GenerationCenterApi;
  readonly initialTaskId?: string;
  readonly query?: URLSearchParams;
  readonly replaceQuery?: (query: URLSearchParams) => void;
}

export function GenerationWorkbench({ scope, view: incomingView = "center", api: providedApi, initialTaskId, query, replaceQuery }: GenerationWorkbenchProps) {
  const { t: submissionText } = useSubmissionTranslation();
  const { t } = useQueueText();
  const sessionEpoch = useAuthStore((auth) => auth.sessionEpoch);
  const api = useMemo(() => providedApi ?? createGenerationCenterApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }), [providedApi, sessionEpoch]);
  const detailController = useRef<AbortController | null>(null);
  const detailScope = useRef<{ api: GenerationCenterApi; workspaceId: string; projectId: string; sessionEpoch: string } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadedScope = useRef<string | undefined>(undefined);
  const view = incomingView === "model-center" || incomingView === "model-detail" || incomingView === "model-market" || incomingView === "model-report" ? "center" : incomingView;
  const config = copy[view];
  const Icon = config.icon;
  const [tasks, setTasks] = useState<readonly GenerationTask[]>([]);
  const [total, setTotal] = useState(0);
  const [localOffset, setOffset] = useState(0);
  const [localFilters, setFilters] = useState<GenerationTaskFilters>({});
  const routeGroup = query?.get("progress");
  const routeMedia = query?.get("media");
  const routeType = query?.get("type");
  const routeFilters = useMemo(() => queueLocation(new URLSearchParams({
    progress: routeGroup ?? "", media: routeMedia ?? "", type: routeType ?? "",
  })).filters, [routeGroup, routeMedia, routeType]);
  const filters = query ? routeFilters : localFilters;
  const offset = query ? queueLocation(query).offset : localOffset;
  const [candidates, setCandidates] = useState<readonly GenerationCandidateSet[]>([]);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [selected, setSelected] = useState<GenerationTaskDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");
  const [message, setMessage] = useState("");
  const [busyId, setBusyId] = useState<string>();
  const controller = useRef<AbortController | null>(null);
  const saveLocation = useCallback((next: GenerationTaskFilters, nextOffset: number, taskId?: string) => {
    if (!query || !replaceQuery) { setFilters(next); setOffset(nextOffset); return; }
    const params = new URLSearchParams(query);
    const changes = { progress: next.group, media: next.mediaType, type: next.capability,
      page: nextOffset ? String(1 + Math.floor(nextOffset / PRODUCTION_PAGE_SIZE)) : undefined, taskId };
    for (const [name, value] of Object.entries(changes)) {
      if (value) params.set(name, value); else params.delete(name);
    }
    if (params.toString() !== query.toString()) replaceQuery(params);
  }, [query, replaceQuery]);
  const actionPending = useRef(false);
  const selectedId = useRef<string | null>(null);
  const load = useCallback(async () => {
    controller.current?.abort();
    const signalController = new AbortController(); controller.current = signalController;
    const scopeKey = `${sessionEpoch}:${scope.workspaceId}:${scope.projectId}:${view}`;
    const hasLoadedScope = loadedScope.current === scopeKey;
    if (!hasLoadedScope) { setState("loading"); setSelected(null); }
    setRefreshing(true);
    try {
      const [page, candidatePage] = await Promise.all([
        api.list(scope.projectId, offset, PRODUCTION_PAGE_SIZE, signalController.signal, filters),
        view === "video-candidates" ? api.candidates(scope.projectId, { mediaType: "video", offset: 0, limit: 20, signal: signalController.signal }) : Promise.resolve(null),
      ]);
      if (signalController.signal.aborted) return false;
      if (offset && offset >= page.total) {
        saveLocation(filters, Math.max(0, Math.floor((page.total - 1) / PRODUCTION_PAGE_SIZE) * PRODUCTION_PAGE_SIZE), selectedId.current ?? undefined);
        return false;
      }
      setTasks(page.tasks); setTotal(page.total); setCandidates(candidatePage?.candidates ?? []); setCandidateTotal(candidatePage?.total ?? 0);
      loadedScope.current = scopeKey; setState("ready");
      return true;
    } catch (error) {
      if (signalController.signal.aborted) return false;
      if (error instanceof GenerationCenterApiError && error.status === 403) {
        loadedScope.current = undefined; setTasks([]); setSelected(null); setState("forbidden");
      } else setState(hasLoadedScope ? "ready" : "error");
      setMessage(i18n.t(error instanceof GenerationCenterApiError && error.status === 403 ? "productionQueue:accessError" : "productionQueue:loadHelp"));
      return false;
    } finally { if (!signalController.signal.aborted) setRefreshing(false); }
  }, [api, filters, offset, sessionEpoch, scope.workspaceId, scope.projectId, view, saveLocation]);
  useEffect(() => {
    const timer = window.setTimeout(() => { void load(); }, 0);
    return () => { window.clearTimeout(timer); controller.current?.abort(); };
  }, [load]);

  const readDetailId = useCallback(async (taskId: string) => {
    const active = detailScope.current;
    if (!active || active.api !== api || active.projectId !== scope.projectId || active.workspaceId !== scope.workspaceId || active.sessionEpoch !== sessionEpoch) return false;
    if (selectedId.current !== taskId) setSelected(null);
    selectedId.current = taskId;
    detailController.current?.abort();
    const abort = new AbortController(); detailController.current = abort;
    try {
      const detail = await api.detail(scope.projectId, taskId, abort.signal);
      if (abort.signal.aborted) return false;
      setSelected(detail); return true;
    } catch (error) {
      if (!abort.signal.aborted) {
        const denied = error instanceof GenerationCenterApiError && [403, 404].includes(error.status ?? 0);
        if (denied) { selectedId.current = null; setSelected(null); }
        setMessage(i18n.t(denied ? "productionQueue:accessError" : "productionQueue:loadHelp"));
      }
      return false;
    }
  }, [api, sessionEpoch, scope.workspaceId, scope.projectId]);
  useEffect(() => {
    detailScope.current = { api, workspaceId: scope.workspaceId, projectId: scope.projectId, sessionEpoch };
    return () => { detailScope.current = null; selectedId.current = null; detailController.current?.abort(); };
  }, [api, sessionEpoch, scope.workspaceId, scope.projectId]);
  const controlled = Boolean(query);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (initialTaskId) { void readDetailId(initialTaskId); }
      else if (controlled) { selectedId.current = null; setSelected(null); }
    }, 0);
    return () => { window.clearTimeout(timer); detailController.current?.abort(); };
  }, [readDetailId, initialTaskId, controlled]);
  const refresh = useCallback(async () => {
    const id = selectedId.current;
    await Promise.all([load(), id ? readDetailId(id) : undefined]);
  }, [load, readDetailId]);
  const hasPending = tasks.some((task) => isProcessingTask(task.status)) || (selected && isProcessingTask(selected.task.status));
  useEffect(() => {
    if (!hasPending || state !== "ready") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !actionPending.current) void refresh();
    }, 12000);
    return () => window.clearInterval(timer);
  }, [hasPending, refresh, state]);
  const readDetail = (task: GenerationTask) => { setMessage(""); return readDetailId(task.task_id); };
  const closeDetail = () => { detailController.current?.abort(); selectedId.current = null; setSelected(null); };
  const selectFromList = (task: GenerationTask) => {
    setMessage("");
    if (query && initialTaskId !== task.task_id) saveLocation(filters, offset, task.task_id);
    else void readDetailId(task.task_id);
  };
  const actionError = (error: unknown) => {
    if (!(error instanceof GenerationCenterApiError)) return t("actionError");
    if (error.code === "GENERATION_INSUFFICIENT_CREDITS") return submissionText("askOwner");
    if (error.code === "GENERATION_WORKSPACE_CONCURRENCY_LIMIT") return t("atCapacity");
    return t(error.status === 403 ? "forbidden" : error.status === 409 ? "conflict" : "actionError");
  };
  const performAction = async (task: GenerationTask, action: ProductionAction) => {
    if (actionPending.current) return;
    actionPending.current = true;
    const active = detailScope.current;
    setBusyId(task.task_id); setMessage("");
    const repeat = action === "retry" ? repeatRequest(scope.projectId, task.task_id) : undefined;
    const key = repeat?.key ?? `xj:${scope.projectId}:${action}:${task.task_id}:${task.version}`;
    try {
      const updated = action === "retry" ? await api.retry(scope.projectId, task, key) : await api.cancel(scope.projectId, task, key, action);
      repeat?.received();
      if (detailScope.current !== active) return;
      const showingOriginal = selectedId.current === task.task_id;
      const showNewTask = showingOriginal && updated.task_id !== task.task_id;
      const changeList = showNewTask && (offset > 0 || !!filters.group);
      if (showNewTask) saveLocation({ ...filters, group: undefined }, 0, updated.task_id);
      await Promise.all([changeList ? undefined : load(), showingOriginal ? readDetailId(updated.task_id) : undefined]);
      if (action === "retry" && detailScope.current === active) setMessage(t("retried"));
    } catch (error) {
      if (detailScope.current !== active) return;
      await refresh();
      if (detailScope.current === active) setMessage(actionError(error));
    } finally { actionPending.current = false; setBusyId(undefined); }
  };
  const selectCandidateForTask = async (taskId: string, assetId: string, expectedVersion: number) => {
    if (actionPending.current) return;
    actionPending.current = true; setBusyId(taskId); setMessage("");
    const active = detailScope.current;
    try {
      await api.selectCandidate(scope.projectId, taskId, assetId, expectedVersion, `xj:${scope.projectId}:candidate:${taskId}:${assetId}`);
      if (detailScope.current === active) { await refresh(); if (detailScope.current === active) setMessage(t("savedChoice")); }
    } catch (error) {
      if (detailScope.current === active) { await refresh(); if (detailScope.current === active) setMessage(actionError(error)); }
    } finally { actionPending.current = false; setBusyId(undefined); }
  };
  const submitted = async (task: GenerationTask) => {
    const [listLoaded, detailLoaded] = await Promise.all([load(), readDetail(task)]);
    if (!listLoaded || !detailLoaded) throw new Error(submissionText("refreshFailed"));
    setMessage(t("submitted"));
  };
  if (state === "loading") return <main className="grid place-items-center gap-2 p-8" aria-busy="true"><LoaderCircle className="animate-spin" /><p>{t("loading")}</p></main>;
  if (state === "forbidden" || state === "error") return <main className="p-6"><section role="alert"><h1>{t("loadError")}</h1><p>{message}</p>{state === "error" && <button className="xj-production-link" type="button" onClick={() => void load()}>{t("refresh")}</button>}</section></main>;
  const HeadingIcon = view === "tasks" ? ClipboardList : Icon;
  return <main className="xj-production-page"><header className="xj-production-title"><HeadingIcon size={23} aria-hidden="true" /><div>
    <h1>{view === "tasks" ? t("title") : config.title}</h1>
    <p>{[scope.projectName, view === "tasks" ? null : view === "center" ? submissionText("centerHelp") : config.description].filter(Boolean).join(" · ")}</p>
  </div></header>
    {message && <p role="status" className="mb-3 rounded-lg border border-white/10 px-4 py-2 text-sm">{message}</p>}
    {view === "video-candidates" && <CandidateComparison scope={scope} candidates={candidates} total={candidateTotal} selecting={Boolean(busyId)} onSelectCandidate={(taskId, assetId, version) => void selectCandidateForTask(taskId, assetId, version)} />}
    {config.submit && <div className="mb-5"><GenerationSubmissionForm key={`${sessionEpoch}:${scope.workspaceId}:${scope.projectId}:${view}`} config={config} scope={scope} api={api} onSubmitted={submitted} /></div>}
    <ProductionQueue scope={scope} tasks={tasks} total={total} offset={offset} filters={filters} selected={selected} refreshing={refreshing} busy={Boolean(busyId)}
      onFilters={(next) => { closeDetail(); setMessage(""); saveLocation(next, 0); }} onPage={(next) => { closeDetail(); saveLocation(filters, next); }}
      onRefresh={() => { setMessage(""); void refresh(); }} onSelect={selectFromList} onClose={() => { closeDetail(); saveLocation(filters, offset); }} onAction={(task, action) => void performAction(task, action)}
      onChoose={(assetId) => { if (selected) void selectCandidateForTask(selected.task.task_id, assetId, selected.selectedCandidate?.version ?? 0); }} />
  </main>;
}
