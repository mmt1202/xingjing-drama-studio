import { useCallback, useEffect, useState } from "react";

import {
  EditingApiError,
  type EditingApi,
  type EditingFinalVideoVersion,
  type EditingFinalVideoSelection,
  type EditingOutputPolicy,
  type EditingPreview,
  type EditingRenderTask,
  type EditingSourceVersion,
  type EditingTimelineSnapshot,
  type RenderProfileInput,
} from "./editing-api";

type LoadState =
  | "loading"
  | "ready"
  | "denied"
  | "missing"
  | "conflict"
  | "unavailable"
  | "failed";

export interface M08EditingWorkspaceProps {
  readonly api: EditingApi;
  readonly projectId: string;
  readonly timelineId?: string;
  readonly pageId?: string;
}

export type M08PageId = "CR-028" | "CR-036" | "CR-037" | "CR-114" | "CR-122";

const pageCopy: Readonly<Record<M08PageId, { eyebrow: string; title: string; description: string }>> = {
  "CR-028": {
    eyebrow: "EDITING SUITE",
    title: "成片剪辑",
    description: "在服务端版本化时间线中组合视频、配音、字幕、音乐、音效和标识。",
  },
  "CR-036": {
    eyebrow: "FINAL RENDER",
    title: "最终合成",
    description: "基于不可变时间线快照提交渲染，并处理取消、失败、重试和产物校验。",
  },
  "CR-037": {
    eyebrow: "VERSION COMPARE",
    title: "成片版本对比",
    description: "比较预览版、审片版、交付版和发布版的内容与输出差异。",
  },
  "CR-114": {
    eyebrow: "TIMELINE PREVIEW",
    title: "时间轴预览",
    description: "查看镜头、配音、字幕、音乐、音效、标记及导出范围。",
  },
  "CR-122": {
    eyebrow: "WATERMARK & AIGC",
    title: "水印与 AIGC 标识",
    description: "配置平台水印、客户水印、AIGC 标识和正式输出模板。",
  },
};

const profiles: Readonly<Record<"1080p" | "4k", RenderProfileInput>> = {
  "1080p": {
    container: "mp4",
    video_codec: "h264",
    audio_codec: "aac",
    width: 1920,
    height: 1080,
    frame_rate_milli: 24_000,
  },
  "4k": {
    container: "mp4",
    video_codec: "h265",
    audio_codec: "aac",
    width: 3840,
    height: 2160,
    frame_rate_milli: 24_000,
  },
};

function stateFor(error: unknown): LoadState {
  if (!(error instanceof EditingApiError)) return "failed";
  if (error.status === 401 || error.status === 403) return "denied";
  if (error.status === 404) return "missing";
  if (error.status === 409) return "conflict";
  if (error.status === 503) return "unavailable";
  return "failed";
}

function messageFor(state: LoadState, error?: unknown) {
  if (state === "denied")
    return "当前会话没有访问这条时间线或提交成片渲染的权限。";
  if (state === "missing")
    return "这条时间线不存在、已删除，或不在当前项目范围内。";
  if (state === "conflict")
    return "时间线版本已变化。请重新读取服务端快照后再提交。";
  if (state === "unavailable")
    return "成片运行时尚未配置；系统没有改用本地或假渲染器。";
  return error instanceof Error ? error.message : "无法读取成片时间线。";
}

function duration(ms: number) {
  const seconds = Math.floor(ms / 1_000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function M08EditingWorkspace({
  api,
  projectId,
  timelineId,
  pageId = "CR-028",
}: M08EditingWorkspaceProps) {
  const copy = pageCopy[pageId as M08PageId] ?? pageCopy["CR-028"];
  const [activeTimelineId, setActiveTimelineId] = useState(timelineId);
  const [state, setState] = useState<LoadState>("loading");
  const [timelines, setTimelines] = useState<readonly EditingPreview[]>([]);
  const [sources, setSources] = useState<readonly EditingSourceVersion[]>([]);
  const [preview, setPreview] = useState<EditingPreview>();
  const [snapshot, setSnapshot] = useState<EditingTimelineSnapshot>();
  const [error, setError] = useState<unknown>();
  const [profileKey, setProfileKey] = useState<"1080p" | "4k">("1080p");
  const [deadlineMinutes, setDeadlineMinutes] = useState<60 | 360 | 1440>(360);
  const [task, setTask] = useState<EditingRenderTask>();
  const [busy, setBusy] = useState(false);
  const [episodeId, setEpisodeId] = useState("");
  const [episodeFilter, setEpisodeFilter] = useState("");
  const [timelineOffset, setTimelineOffset] = useState(0);
  const [sourceVersionId, setSourceVersionId] = useState("");
  const [selectedTrackId, setSelectedTrackId] = useState("");
  const [selectedClipId, setSelectedClipId] = useState("");
  const [replacementVersionId, setReplacementVersionId] = useState("");
  const [additionalSourceVersionId, setAdditionalSourceVersionId] =
    useState("");
  const [clipDraft, setClipDraft] = useState({
    sourceInMs: 0,
    sourceOutMs: 1,
    timelineStartMs: 0,
    timelineEndMs: 1,
    volumeMilli: 1_000,
    transition: "none",
  });
  const [versions, setVersions] = useState<
    readonly EditingFinalVideoVersion[]
  >([]);
  const [baselineVersionId, setBaselineVersionId] = useState("");
  const [candidateVersionId, setCandidateVersionId] = useState("");
  const [finalSelection, setFinalSelection] =
    useState<EditingFinalVideoSelection | null>(null);
  const [outputPolicy, setOutputPolicy] = useState<EditingOutputPolicy>({
    platform_watermark_enabled: true,
    customer_watermark_text: "",
    watermark_position: "bottom_right",
    watermark_opacity_milli: 700,
    aigc_label_enabled: true,
    aigc_label_style: "visible_and_metadata",
    export_template: "preview",
  });

  const reloadList = useCallback(async () => {
    try {
      const [nextTimelines, nextSources] = await Promise.all([
        api.listTimelines(projectId, {
          offset: timelineOffset,
          limit: 20,
          episodeId: episodeFilter.trim() || undefined,
        }),
        api.listSources(projectId),
      ]);
      setTimelines(nextTimelines);
      setSources(nextSources);
      if (!activeTimelineId) setState("ready");
      if (pageId === "CR-037") {
        const nextVersions = await api.listFinalVideoVersions(projectId);
        const finalVideoId = nextVersions[0]?.final_video_id;
        const comparableVersions = finalVideoId
          ? nextVersions.filter(
              (version) => version.final_video_id === finalVideoId,
            )
          : [];
        setVersions(comparableVersions);
        setBaselineVersionId(
          (current) => current || comparableVersions[1]?.version_id || "",
        );
        setCandidateVersionId(
          (current) => current || comparableVersions[0]?.version_id || "",
        );
        setFinalSelection(
          finalVideoId
            ? await api.getFinalVideoSelection(projectId, finalVideoId)
            : null,
        );
      }
    } catch (cause) {
      setError(cause);
      setState(stateFor(cause));
    }
  }, [
    activeTimelineId,
    api,
    episodeFilter,
    pageId,
    projectId,
    timelineOffset,
  ]);

  const reload = useCallback(async () => {
    if (!activeTimelineId) return;
    setState("loading");
    setError(undefined);
    try {
      const [nextPreview, nextSnapshot] = await Promise.all([
        api.getPreview(projectId, activeTimelineId),
        api.getTimeline(projectId, activeTimelineId),
      ]);
      setPreview(nextPreview);
      setSnapshot(nextSnapshot);
      setOutputPolicy(nextSnapshot.output_policy);
      setSelectedTrackId(
        (current) => current || nextSnapshot.tracks[0]?.track_id || "",
      );
      setSelectedClipId(
        (current) => current || nextSnapshot.tracks[0]?.clips[0]?.clip_id || "",
      );
      setState("ready");
      void reloadList();
    } catch (cause) {
      setError(cause);
      setState(stateFor(cause));
    }
  }, [activeTimelineId, api, projectId, reloadList]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void reloadList();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [reloadList]);
  useEffect(() => {
    if (!activeTimelineId) return;
    const timer = window.setTimeout(() => {
      void reload();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [activeTimelineId, reload]);

  const chooseTimeline = (id: string) => {
    setActiveTimelineId(id);
    setPreview(undefined);
    setSnapshot(undefined);
    setTask(undefined);
    setSelectedTrackId("");
    setSelectedClipId("");
  };

  const createTimeline = async () => {
    const selectedSource = sources.find(
      (source) => source.version_id === sourceVersionId,
    );
    if (!episodeId.trim() || !selectedSource) {
      setError(
        new Error("请填写剧集 ID，并选择服务端确认可用的 M06 视频素材。"),
      );
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const created = await api.createTimeline(projectId, {
        episodeId: episodeId.trim(),
        sourceVersionId: selectedSource.version_id,
        sourceOutMs: selectedSource.duration_ms,
      });
      setTimelines((current) => [
        created,
        ...current.filter((item) => item.timeline_id !== created.timeline_id),
      ]);
      chooseTimeline(created.timeline_id);
      setEpisodeId("");
      setSourceVersionId("");
    } catch (cause) {
      setError(cause);
      setState(stateFor(cause));
    } finally {
      setBusy(false);
    }
  };

  const replaceClip = async () => {
    if (!snapshot || !selectedTrackId || !selectedClipId) return;
    const selectedSource = sources.find(
      (source) => source.version_id === replacementVersionId,
    );
    if (!selectedSource) {
      setError(new Error("请选择服务端确认可用的替换视频素材。"));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.replaceClip(projectId, snapshot.timeline_id, {
        trackId: selectedTrackId,
        clipId: selectedClipId,
        sourceVersionId: selectedSource.version_id,
        sourceInMs: 0,
        sourceOutMs: selectedSource.duration_ms,
        expectedRevision: snapshot.revision,
      });
      setReplacementVersionId("");
      await reload();
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await reload();
      else setState(stateFor(cause));
    } finally {
      setBusy(false);
    }
  };

  const updateClip = async () => {
    if (!snapshot || !selectedTrackId || !selectedClipId) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.updateClip(projectId, snapshot.timeline_id, {
        trackId: selectedTrackId,
        clipId: selectedClipId,
        sourceInMs: clipDraft.sourceInMs,
        sourceOutMs: clipDraft.sourceOutMs,
        timelineStartMs: clipDraft.timelineStartMs,
        timelineEndMs: clipDraft.timelineEndMs,
        volumeMilli: clipDraft.volumeMilli,
        effects:
          clipDraft.transition === "none"
            ? {}
            : { transition: clipDraft.transition },
        expectedRevision: snapshot.revision,
      });
      await reload();
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await reload();
      else setState(stateFor(cause));
    } finally {
      setBusy(false);
    }
  };

  const addAudioTrack = async () => {
    if (!snapshot || busy) return;
    const source = sources.find(
      (item) => item.version_id === additionalSourceVersionId,
    );
    if (!source || source.media_kind === "video") {
      setError(new Error("请选择 M07 已完成的配音、字幕、BGM 或音效版本。"));
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.addTrack(
        projectId,
        snapshot.timeline_id,
        snapshot.revision,
        source,
      );
      setAdditionalSourceVersionId("");
      await reload();
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await reload();
      else setState(stateFor(cause));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!preview || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const deadlineAt = new Date(
        Date.now() + deadlineMinutes * 60_000,
      ).toISOString();
      setTask(
        await api.submitRender(projectId, {
          preview,
          profile: profiles[profileKey],
          deadlineAt,
          maxAttempts: 3,
        }),
      );
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await reload();
    } finally {
      setBusy(false);
    }
  };

  const refreshTask = async () => {
    if (!task) return;
    try {
      setTask(await api.getRenderTask(projectId, task.task_id));
      setError(undefined);
    } catch (cause) {
      setError(cause);
    }
  };

  const cancelTask = async () => {
    if (!task || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      setTask(
        await api.cancelRender(
          projectId,
          task.task_id,
          task.task_revision,
          "用户从成片工作台取消",
        ),
      );
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await refreshTask();
    } finally {
      setBusy(false);
    }
  };

  const retryTask = async () => {
    if (!task || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      setTask(
        await api.retryRender(projectId, task.task_id, task.task_revision),
      );
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await refreshTask();
    } finally {
      setBusy(false);
    }
  };

  const saveOutputPolicy = async () => {
    if (!snapshot || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const updated = await api.updateOutputPolicy(
        projectId,
        snapshot.timeline_id,
        snapshot.revision,
        outputPolicy,
      );
      setSnapshot(updated);
      await reload();
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") await reload();
      else setState(stateFor(cause));
    } finally {
      setBusy(false);
    }
  };

  const keepCandidateVersion = async () => {
    if (!candidateVersion || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      setFinalSelection(
        await api.selectFinalVideoVersion(
          projectId,
          candidateVersion.final_video_id,
          candidateVersion.version_id,
          finalSelection?.revision ?? 0,
        ),
      );
    } catch (cause) {
      setError(cause);
      if (stateFor(cause) === "conflict") {
        setFinalSelection(
          await api.getFinalVideoSelection(
            projectId,
            candidateVersion.final_video_id,
          ),
        );
      }
    } finally {
      setBusy(false);
    }
  };

  if (!activeTimelineId)
    return (
      <main className="min-h-screen bg-[#090b10] p-6 text-slate-100">
        <div className="mx-auto max-w-6xl">
          <header className="border-b border-slate-800 pb-6">
            <p className="font-mono text-xs tracking-[0.24em] text-cyan-300">
              M08 · {copy.eyebrow}
            </p>
            <h1 className="mt-3 text-3xl font-semibold">{copy.title}</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              {copy.description}
            </p>
          </header>
          {state !== "ready" ? (
            <p className="mt-6 text-slate-400">
              {state === "loading"
                ? "正在读取项目时间线与素材…"
                : messageFor(state, error)}
            </p>
          ) : (
            <div className="mt-6 grid gap-6 lg:grid-cols-2">
              <section className="border border-slate-800 bg-slate-950 p-5">
                <h2 className="font-semibold">项目时间线</h2>
                <label className="mt-4 block text-sm text-slate-300">
                  按剧集 ID 筛选
                  <input
                    value={episodeFilter}
                    onChange={(event) => {
                      setEpisodeFilter(event.target.value);
                      setTimelineOffset(0);
                    }}
                    className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                  />
                </label>
                {timelines.length ? (
                  <div className="mt-4 grid gap-3">
                    {timelines.map((item) => (
                      <button
                        key={item.timeline_id}
                        type="button"
                        onClick={() => chooseTimeline(item.timeline_id)}
                        className="border border-slate-700 p-4 text-left hover:border-cyan-300"
                      >
                        <strong className="font-mono text-sm text-cyan-100">
                          {item.timeline_id}
                        </strong>
                        <p className="mt-2 text-sm text-slate-300">
                          剧集 {item.episode_id} · 版本 {item.timeline_revision} ·{" "}
                          {duration(item.duration_ms)} · {item.track_count}{" "}
                          条轨道 · {item.clip_count} 个片段
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          更新于{" "}
                          {new Date(item.updated_at).toLocaleString("zh-CN")}
                        </p>
                        <p className="mt-1 break-all font-mono text-[11px] text-slate-500">
                          {item.timeline_version_id}
                        </p>
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="mt-4 text-sm text-slate-400">
                    当前项目还没有时间线。请用右侧表单从已完成的视频资产建立第一条。
                  </p>
                )}
                <div className="mt-4 flex items-center justify-between">
                  <button
                    type="button"
                    disabled={timelineOffset === 0}
                    onClick={() =>
                      setTimelineOffset((current) => Math.max(0, current - 20))
                    }
                    className="border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
                  >
                    上一页
                  </button>
                  <span className="text-xs text-slate-500">
                    第 {Math.floor(timelineOffset / 20) + 1} 页
                  </span>
                  <button
                    type="button"
                    disabled={timelines.length < 20}
                    onClick={() =>
                      setTimelineOffset((current) => current + 20)
                    }
                    className="border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
                  >
                    下一页
                  </button>
                </div>
              </section>
              <section
                className={`${pageId === "CR-028" ? "" : "hidden "}border border-cyan-300/25 bg-cyan-300/[0.03] p-5`}
              >
                <p className="font-mono text-xs tracking-[0.2em] text-cyan-300">
                  CREATE TIMELINE
                </p>
                <h2 className="mt-2 font-semibold">从已完成视频创建时间线</h2>
                <label className="mt-5 block text-sm text-slate-300">
                  剧集 ID
                  <input
                    value={episodeId}
                    onChange={(event) => setEpisodeId(event.target.value)}
                    className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                  />
                </label>
                <label className="mt-4 block text-sm text-slate-300">
                  可用 M06 视频素材
                  <select
                    value={sourceVersionId}
                    onChange={(event) => setSourceVersionId(event.target.value)}
                    className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                  >
                    <option value="">请选择服务端素材</option>
                    {sources
                      .filter((source) => source.media_kind === "video")
                      .map((source) => (
                      <option key={source.version_id} value={source.version_id}>
                        {source.asset_id} · {duration(source.duration_ms)}
                      </option>
                      ))}
                  </select>
                </label>
                {!sources.some((source) => source.media_kind === "video") ? (
                  <p className="mt-3 text-xs leading-5 text-amber-200">
                    当前没有可用于剪辑的视频。请先完成 M06 视频生成，Provider
                    回调必须包含 durationMs。
                  </p>
                ) : null}
                <button
                  type="button"
                  disabled={
                    busy ||
                    !sources.some((source) => source.media_kind === "video")
                  }
                  onClick={() => void createTimeline()}
                  className="mt-5 w-full bg-cyan-300 px-4 py-3 text-sm font-semibold text-slate-950 disabled:bg-slate-600"
                >
                  {busy ? "正在建立服务端时间线…" : "创建视频时间线"}
                </button>
              </section>
            </div>
          )}
          {error ? (
            <p
              role="alert"
              className="mt-6 border border-rose-400/40 bg-rose-400/10 p-3 text-sm text-rose-100"
            >
              {messageFor(stateFor(error), error)}
            </p>
          ) : null}
        </div>
      </main>
    );

  if (state !== "ready" || !preview || !snapshot)
    return (
      <main className="min-h-screen bg-[#090b10] p-6 text-slate-100">
        <section
          className="mx-auto max-w-3xl border border-slate-700 bg-slate-950 p-6"
          aria-live="polite"
          aria-busy={state === "loading"}
        >
          <p className="font-mono text-xs tracking-[0.24em] text-cyan-300">
            M08 · RENDER CONTROL
          </p>
          <h1 className="mt-3 text-2xl font-semibold">成片时间线</h1>
          <p className="mt-4 text-sm text-slate-400">
            {state === "loading"
              ? "正在读取服务端时间线快照…"
              : messageFor(state, error)}
          </p>
          <button
            type="button"
            onClick={() => {
              setActiveTimelineId(undefined);
              void reloadList();
            }}
            className="mt-5 border border-slate-600 px-4 py-2 text-sm hover:border-cyan-300"
          >
            返回时间线列表
          </button>
        </section>
      </main>
    );

  const activeTrack = snapshot.tracks.find(
    (track) => track.track_id === selectedTrackId,
  );
  const activeClip = activeTrack?.clips.find(
    (clip) => clip.clip_id === selectedClipId,
  );
  const baselineVersion = versions.find(
    (version) => version.version_id === baselineVersionId,
  );
  const candidateVersion = versions.find(
    (version) => version.version_id === candidateVersionId,
  );
  return (
    <main className="min-h-screen bg-[#090b10] p-6 text-slate-100">
      <div className="mx-auto max-w-6xl">
        <header className="border-b border-slate-800 pb-6">
          <p className="font-mono text-xs tracking-[0.24em] text-cyan-300">
            M08 · {copy.eyebrow}
          </p>
          <div className="mt-3 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold tracking-tight">
                {copy.title}
              </h1>
              <p className="mt-2 font-mono text-xs text-slate-500">
                TIMELINE {preview.timeline_id} · VERSION{" "}
                {preview.timeline_version_id}
              </p>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  setActiveTimelineId(undefined);
                  void reloadList();
                }}
                className="border border-slate-600 px-3 py-2 text-sm hover:border-cyan-300"
              >
                全部时间线
              </button>
              <button
                type="button"
                onClick={() => void reload()}
                className="border border-slate-600 px-3 py-2 text-sm hover:border-cyan-300"
              >
                刷新权威快照
              </button>
            </div>
          </div>
        </header>
        <section className="mt-6 grid gap-px border border-slate-800 bg-slate-800 md:grid-cols-4">
          <Metric label="时长" value={duration(preview.duration_ms)} />
          <Metric label="轨道" value={String(preview.track_count)} />
          <Metric label="片段" value={String(preview.clip_count)} />
          <Metric label="修订" value={String(preview.timeline_revision)} />
        </section>
        {pageId === "CR-037" ? (
          <section className="mt-6 border border-violet-300/25 bg-violet-300/[0.03] p-5">
            <p className="font-mono text-xs tracking-[0.2em] text-violet-200">
              VERSION COMPARE
            </p>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <VersionSelector
                label="基准版本"
                versions={versions}
                value={baselineVersionId}
                onChange={setBaselineVersionId}
              />
              <VersionSelector
                label="候选版本"
                versions={versions}
                value={candidateVersionId}
                onChange={setCandidateVersionId}
              />
            </div>
            {baselineVersion && candidateVersion ? (
              <>
                <div className="mt-5 grid gap-3 text-sm md:grid-cols-3">
                  <Metric
                    label="分辨率变化"
                    value={`${baselineVersion.profile.width}×${baselineVersion.profile.height} → ${candidateVersion.profile.width}×${candidateVersion.profile.height}`}
                  />
                  <Metric
                    label="时长变化"
                    value={`${duration(baselineVersion.output.duration_ms)} → ${duration(candidateVersion.output.duration_ms)}`}
                  />
                  <Metric
                    label="内容摘要"
                    value={
                      baselineVersion.output.content_sha256 ===
                      candidateVersion.output.content_sha256
                        ? "内容相同"
                        : "内容不同"
                    }
                  />
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <button
                    type="button"
                    disabled={
                      busy ||
                      finalSelection?.selected_version_id ===
                        candidateVersion.version_id
                    }
                    onClick={() => void keepCandidateVersion()}
                    className="border border-violet-300 px-4 py-2 text-violet-100 disabled:opacity-50"
                  >
                    保留候选版本
                  </button>
                  <span className="text-xs text-slate-400">
                    当前保留：
                    {finalSelection?.selected_version_id ?? "尚未选择"} · 修订{" "}
                    {finalSelection?.revision ?? 0}
                  </span>
                </div>
              </>
            ) : (
              <p className="mt-4 text-sm text-slate-400">
                至少需要两个已验证成片版本才能进行对比。
              </p>
            )}
          </section>
        ) : null}
        {pageId === "CR-122" ? (
          <section className="mt-6 border border-emerald-300/25 bg-emerald-300/[0.03] p-5">
            <p className="font-mono text-xs tracking-[0.2em] text-emerald-200">
              OUTPUT POLICY
            </p>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <BooleanField
                label="平台水印"
                checked={outputPolicy.platform_watermark_enabled}
                onChange={(checked) =>
                  setOutputPolicy((current) => ({
                    ...current,
                    platform_watermark_enabled: checked,
                  }))
                }
              />
              <BooleanField
                label="AIGC 可见/元数据标识"
                checked={outputPolicy.aigc_label_enabled}
                onChange={(checked) =>
                  setOutputPolicy((current) => ({
                    ...current,
                    aigc_label_enabled: checked,
                  }))
                }
              />
              <label className="text-sm text-slate-300">
                客户水印文字
                <input
                  maxLength={120}
                  value={outputPolicy.customer_watermark_text}
                  onChange={(event) =>
                    setOutputPolicy((current) => ({
                      ...current,
                      customer_watermark_text: event.target.value,
                    }))
                  }
                  className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                />
              </label>
              <label className="text-sm text-slate-300">
                水印位置
                <select
                  value={outputPolicy.watermark_position}
                  onChange={(event) =>
                    setOutputPolicy((current) => ({
                      ...current,
                      watermark_position: event.target
                        .value as EditingOutputPolicy["watermark_position"],
                    }))
                  }
                  className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                >
                  <option value="top_left">左上</option>
                  <option value="top_right">右上</option>
                  <option value="bottom_left">左下</option>
                  <option value="bottom_right">右下</option>
                  <option value="center">居中</option>
                </select>
              </label>
              <NumberField
                label="水印不透明度（0–1000）"
                value={outputPolicy.watermark_opacity_milli}
                onChange={(value) =>
                  setOutputPolicy((current) => ({
                    ...current,
                    watermark_opacity_milli: Math.min(1_000, value),
                  }))
                }
              />
              <label className="text-sm text-slate-300">
                输出模板
                <select
                  value={outputPolicy.export_template}
                  onChange={(event) =>
                    setOutputPolicy((current) => ({
                      ...current,
                      export_template: event.target
                        .value as EditingOutputPolicy["export_template"],
                    }))
                  }
                  className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                >
                  <option value="preview">预览版</option>
                  <option value="review">审片版</option>
                  <option value="delivery">交付版</option>
                  <option value="platform">平台发布版</option>
                </select>
              </label>
            </div>
            <button
              type="button"
              disabled={busy}
              onClick={() => void saveOutputPolicy()}
              className="mt-5 bg-emerald-200 px-4 py-2 font-semibold text-slate-950 disabled:opacity-50"
            >
              {busy ? "正在保存版本化策略…" : "保存水印与 AIGC 策略"}
            </button>
          </section>
        ) : null}
        <div className="mt-6 grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
          <section
            className={`${["CR-028", "CR-114"].includes(pageId) ? "" : "hidden "}border border-slate-800 bg-slate-950 p-5`}
          >
            <h2 className="font-semibold">时间线轨道与片段</h2>
            <p className="mt-2 text-sm text-slate-400">
              选择片段后可用另一条 M06 已完成视频替换。替换采用当前修订号
              CAS；服务端会保存新版本而不覆盖历史。
            </p>
            <div className="mt-5 grid gap-3">
              {snapshot.tracks.map((track) => (
                <article
                  key={track.track_id}
                  className="border border-slate-800 p-3"
                >
                  <p className="font-mono text-xs text-cyan-200">
                    {track.kind.toUpperCase()} · {track.track_id}
                  </p>
                  {track.clips.map((clip) => (
                    <button
                      key={clip.clip_id}
                      type="button"
                      onClick={() => {
                        setSelectedTrackId(track.track_id);
                        setSelectedClipId(clip.clip_id);
                        setClipDraft({
                          sourceInMs: clip.source_in_ms,
                          sourceOutMs: clip.source_out_ms,
                          timelineStartMs: clip.timeline_start_ms,
                          timelineEndMs: clip.timeline_end_ms,
                          volumeMilli: clip.volume_milli ?? 1_000,
                          transition:
                            typeof clip.effects?.transition === "string"
                              ? clip.effects.transition
                              : "none",
                        });
                      }}
                      className={`mt-2 block w-full border p-3 text-left text-sm ${clip.clip_id === selectedClipId ? "border-cyan-300 bg-cyan-300/10" : "border-slate-700 hover:border-slate-500"}`}
                    >
                      <strong className="font-mono text-xs">
                        {clip.clip_id}
                      </strong>
                      <p className="mt-1 text-slate-300">
                        源 {clip.source_version_id} ·{" "}
                        {duration(clip.source_in_ms)}–
                        {duration(clip.source_out_ms)} → 时间线{" "}
                        {duration(clip.timeline_start_ms)}–
                        {duration(clip.timeline_end_ms)}
                      </p>
                    </button>
                  ))}
                </article>
              ))}
            </div>
            {pageId === "CR-028" ? (
              <div className="mt-5 border border-cyan-300/25 p-4">
                <h3 className="font-semibold text-cyan-100">
                  接入 M07 配音、字幕、BGM 或音效
                </h3>
                <select
                  value={additionalSourceVersionId}
                  onChange={(event) =>
                    setAdditionalSourceVersionId(event.target.value)
                  }
                  className="mt-3 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                >
                  <option value="">请选择已完成音频版本</option>
                  {sources
                    .filter((source) => source.media_kind !== "video")
                    .map((source) => (
                      <option
                        key={source.version_id}
                        value={source.version_id}
                      >
                        {source.media_kind.toUpperCase()} · {source.asset_id} ·{" "}
                        {duration(source.duration_ms)}
                      </option>
                    ))}
                </select>
                <button
                  type="button"
                  disabled={busy || !additionalSourceVersionId}
                  onClick={() => void addAudioTrack()}
                  className="mt-3 border border-cyan-300 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50"
                >
                  添加版本化音轨
                </button>
              </div>
            ) : null}
            {activeClip ? (
              <div className="mt-5 border border-amber-300/25 bg-amber-300/[0.04] p-4">
                <h3 className="font-semibold text-amber-100">调整所选片段</h3>
                <p className="mt-2 text-xs text-slate-400">
                  {activeTrack?.track_id} / {activeClip.clip_id}，当前源{" "}
                  {activeClip.source_version_id}
                </p>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <NumberField
                    label="源入点（毫秒）"
                    value={clipDraft.sourceInMs}
                    onChange={(value) =>
                      setClipDraft((current) => ({
                        ...current,
                        sourceInMs: value,
                      }))
                    }
                  />
                  <NumberField
                    label="源出点（毫秒）"
                    value={clipDraft.sourceOutMs}
                    onChange={(value) =>
                      setClipDraft((current) => ({
                        ...current,
                        sourceOutMs: value,
                      }))
                    }
                  />
                  <NumberField
                    label="时间线起点（毫秒）"
                    value={clipDraft.timelineStartMs}
                    onChange={(value) =>
                      setClipDraft((current) => ({
                        ...current,
                        timelineStartMs: value,
                      }))
                    }
                  />
                  <NumberField
                    label="时间线终点（毫秒）"
                    value={clipDraft.timelineEndMs}
                    onChange={(value) =>
                      setClipDraft((current) => ({
                        ...current,
                        timelineEndMs: value,
                      }))
                    }
                  />
                  <NumberField
                    label="音量（0–2000）"
                    value={clipDraft.volumeMilli}
                    onChange={(value) =>
                      setClipDraft((current) => ({
                        ...current,
                        volumeMilli: value,
                      }))
                    }
                  />
                  <label className="text-sm text-slate-300">
                    转场效果
                    <select
                      value={clipDraft.transition}
                      onChange={(event) =>
                        setClipDraft((current) => ({
                          ...current,
                          transition: event.target.value,
                        }))
                      }
                      className="mt-1 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
                    >
                      <option value="none">无</option>
                      <option value="fade">淡入淡出</option>
                      <option value="dissolve">叠化</option>
                      <option value="wipe">擦除</option>
                    </select>
                  </label>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void updateClip()}
                  className="mt-4 border border-amber-200 px-4 py-2 text-sm font-semibold text-amber-100 disabled:opacity-50"
                >
                  {busy ? "正在保存新版本…" : "保存片段调整"}
                </button>
                <h3 className="mt-6 font-semibold text-amber-100">替换素材版本</h3>
                <label className="mt-3 block text-sm text-slate-300">
                  替换视频素材
                  <select
                    value={replacementVersionId}
                    onChange={(event) =>
                      setReplacementVersionId(event.target.value)
                    }
                    className="mt-1 block w-full border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
                  >
                    <option value="">请选择服务端素材</option>
                    {sources
                      .filter(
                        (source) =>
                          source.version_id !== activeClip.source_version_id &&
                          source.media_kind === activeTrack?.kind,
                      )
                      .map((source) => (
                        <option
                          key={source.version_id}
                          value={source.version_id}
                        >
                          {source.asset_id} · {duration(source.duration_ms)}
                        </option>
                      ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || !replacementVersionId}
                  onClick={() => void replaceClip()}
                  className="mt-4 bg-amber-200 px-4 py-2 text-sm font-semibold text-slate-950 disabled:bg-slate-600"
                >
                  {busy ? "正在保存新版本…" : "替换片段并保存版本"}
                </button>
              </div>
            ) : null}
          </section>
          <section
            className={`${["CR-028", "CR-036"].includes(pageId) ? "" : "hidden "}border border-cyan-300/25 bg-cyan-300/[0.03] p-5`}
          >
            <p className="font-mono text-xs tracking-[0.2em] text-cyan-300">
              SUBMIT RENDER
            </p>
            <h2 className="mt-2 font-semibold">提交正式渲染</h2>
            <label className="mt-5 block text-sm text-slate-300">
              输出预设
              <select
                value={profileKey}
                onChange={(event) =>
                  setProfileKey(event.target.value as "1080p" | "4k")
                }
                className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              >
                <option value="1080p">1080p · H.264 / AAC</option>
                <option value="4k">4K · H.265 / AAC</option>
              </select>
            </label>
            <label className="mt-4 block text-sm text-slate-300">
              最长等待
              <select
                value={deadlineMinutes}
                onChange={(event) =>
                  setDeadlineMinutes(
                    Number(event.target.value) as 60 | 360 | 1440,
                  )
                }
                className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
              >
                <option value={60}>1 小时</option>
                <option value={360}>6 小时</option>
                <option value={1440}>24 小时</option>
              </select>
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={() => void submit()}
              className="mt-5 w-full bg-cyan-300 px-4 py-3 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:bg-slate-600"
            >
              {busy ? "正在提交服务端渲染…" : "提交渲染任务"}
            </button>
            <p className="mt-3 text-xs leading-5 text-slate-500">
              渲染绑定当前不可变时间线快照；渲染器或对象存储缺失时服务端会明确拒绝，不会假装输出成片。
            </p>
          </section>
        </div>
        {task && ["CR-028", "CR-036"].includes(pageId) ? (
          <section
            className="mt-6 border border-slate-800 bg-slate-950 p-5"
            aria-live="polite"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-mono text-xs text-cyan-300">
                  TASK {task.task_id}
                </p>
                <h2 className="mt-1 text-lg font-semibold">
                  状态：{task.status}
                </h2>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void refreshTask()}
                  className="border border-slate-600 px-3 py-2 text-sm hover:border-cyan-300"
                >
                  刷新任务状态
                </button>
                {["queued", "running", "retrying"].includes(task.status) ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void cancelTask()}
                    className="border border-rose-400/60 px-3 py-2 text-sm text-rose-100 disabled:opacity-50"
                  >
                    取消渲染
                  </button>
                ) : null}
                {task.status === "failed" && task.failure?.retryable ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void retryTask()}
                    className="border border-amber-300/60 px-3 py-2 text-sm text-amber-100 disabled:opacity-50"
                  >
                    重试渲染
                  </button>
                ) : null}
              </div>
            </div>
            <p className="mt-3 text-sm text-slate-400">
              尝试 {task.attempt}/{task.max_attempts} · 截止{" "}
              {new Date(task.deadline_at).toLocaleString("zh-CN")}
            </p>
            <p className="mt-2 text-sm text-slate-400">
              费用：{task.billing_currency}{" "}
              {(
                (task.billing_actual_minor ?? task.billing_estimated_minor) /
                100
              ).toFixed(2)}{" "}
              · {task.billing_status} · {task.billing_pricing_version}
            </p>
            {task.failure ? (
              <p
                role="alert"
                className="mt-3 border-l-2 border-rose-400 pl-3 text-sm text-rose-200"
              >
                {task.failure.code}：{task.failure.message}
              </p>
            ) : null}
          </section>
        ) : null}
        {error ? (
          <p
            role="alert"
            className="mt-6 border border-rose-400/40 bg-rose-400/10 p-3 text-sm text-rose-100"
          >
            {messageFor(stateFor(error), error)}
          </p>
        ) : null}
      </div>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#0d1018] p-4">
      <p className="font-mono text-xs text-slate-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-cyan-100">{value}</p>
    </div>
  );
}

function VersionSelector({
  label,
  versions,
  value,
  onChange,
}: {
  label: string;
  versions: readonly EditingFinalVideoVersion[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-sm text-slate-300">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
      >
        <option value="">请选择成片版本</option>
        {versions.map((version) => (
          <option key={version.version_id} value={version.version_id}>
            {version.version_id} ·{" "}
            {new Date(version.created_at).toLocaleString("zh-CN")}
          </option>
        ))}
      </select>
    </label>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="text-sm text-slate-300">
      {label}
      <input
        type="number"
        min={0}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-1 block w-full border border-slate-700 bg-slate-950 px-3 py-2"
      />
    </label>
  );
}

function BooleanField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between border border-slate-700 px-3 py-2 text-sm text-slate-300">
      {label}
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}
