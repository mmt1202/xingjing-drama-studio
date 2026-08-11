import { useCallback, useEffect, useRef, useState } from "react";
import { generationAudioApi, XingjingApiError } from "./api";
import {
  createIdempotencyKey,
  type AudioTrack,
  type AudioTrackKind,
  type LipSyncVersion,
  type MediaTask,
  type MediaTaskKind,
  type SubtitleFormat,
  type SubtitleTrack,
  type TrackVersion,
} from "./contracts";

type ApiClient = typeof generationAudioApi;
export type AudioPageId =
  "CR-007" | "CR-008" | "CR-056" | "CR-057" | "CR-058" | "CR-059" | "CR-105";
export interface GenerationAudioWorkspaceProps {
  projectId: string;
  projectName?: string;
  api?: ApiClient;
  pageId?: AudioPageId;
}
type LoadState = "loading" | "ready" | "forbidden" | "error";
type SelectedTrack =
  | { kind: "audio"; track: AudioTrack }
  | { kind: "subtitle"; track: SubtitleTrack };
type AudioSection =
  | "create"
  | "tracks"
  | "lip-create"
  | "lip-compare"
  | "version"
  | "task-create"
  | "task-cancel"
  | "tasks";

const PAGE_CONFIG: Record<
  AudioPageId,
  {
    title: string;
    description: string;
    sections: ReadonlySet<AudioSection>;
    taskKinds?: ReadonlySet<MediaTaskKind>;
  }
> = {
  "CR-007": {
    title: "音频与字幕工作台",
    description: "管理配音、音轨、字幕轨、不可变版本与媒体任务。",
    sections: new Set([
      "create",
      "tracks",
      "version",
      "task-create",
      "task-cancel",
      "tasks",
    ]),
  },
  "CR-008": {
    title: "BGM 与音效混音",
    description: "管理 BGM、音效与混音轨，并提交真实混音任务。",
    sections: new Set([
      "create",
      "tracks",
      "version",
      "task-create",
      "task-cancel",
      "tasks",
    ]),
    taskKinds: new Set(["mix"]),
  },
  "CR-056": {
    title: "对口型生成检查",
    description: "核对音频版本、字幕版本、输入视频和校准参数后创建候选。",
    sections: new Set(["tracks", "lip-create", "tasks"]),
    taskKinds: new Set(["lip_sync"]),
  },
  "CR-057": {
    title: "对口型失败与回退",
    description: "处理失败、超时和取消任务，支持重试与保留原版本。",
    sections: new Set(["lip-compare", "task-cancel", "tasks"]),
    taskKinds: new Set(["lip_sync"]),
  },
  "CR-058": {
    title: "对口型版本对比",
    description: "对比原视频与候选版本，并持久化最终选择。",
    sections: new Set(["lip-compare"]),
  },
  "CR-059": {
    title: "对口型工作台",
    description: "创建、生成、保存并选择真实对口型版本。",
    sections: new Set([
      "tracks",
      "lip-create",
      "lip-compare",
      "task-create",
      "task-cancel",
      "tasks",
    ]),
    taskKinds: new Set(["lip_sync"]),
  },
  "CR-105": {
    title: "字幕时间轴",
    description: "维护字幕轨、时间码内容和不可变字幕版本。",
    sections: new Set(["create", "tracks", "version", "task-create", "tasks"]),
    taskKinds: new Set(["subtitle"]),
  },
};

function newId() {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}
function errorText(error: unknown) {
  if (error instanceof XingjingApiError)
    return `${error.detail.message}${error.detail.requestId ? `（请求 ${error.detail.requestId}）` : ""}`;
  return "音频服务暂时不可用，请稍后重试。";
}
function timestamp(value?: string) {
  return value ? new Date(value).toLocaleString() : "—";
}

export function GenerationAudioWorkspace({
  projectId,
  projectName,
  api = generationAudioApi,
  pageId = "CR-007",
}: GenerationAudioWorkspaceProps) {
  const page = PAGE_CONFIG[pageId];
  const [state, setState] = useState<LoadState>("loading");
  const [audioTracks, setAudioTracks] = useState<AudioTrack[]>([]);
  const [subtitleTracks, setSubtitleTracks] = useState<SubtitleTrack[]>([]);
  const [mediaTasks, setMediaTasks] = useState<MediaTask[]>([]);
  const [trackTotal, setTrackTotal] = useState(0);
  const [taskTotal, setTaskTotal] = useState(0);
  const [trackPage, setTrackPage] = useState(0);
  const [taskPage, setTaskPage] = useState(0);
  const [trackQuery, setTrackQuery] = useState("");
  const [appliedTrackQuery, setAppliedTrackQuery] = useState("");
  const [trackKindFilter, setTrackKindFilter] = useState(
    pageId === "CR-008" ? "bgm" : "",
  );
  const [trackStateFilter, setTrackStateFilter] = useState("");
  const [taskQuery, setTaskQuery] = useState("");
  const [appliedTaskQuery, setAppliedTaskQuery] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState("");
  const [lipSyncVersions, setLipSyncVersions] = useState<LipSyncVersion[]>([]);
  const [trackVersions, setTrackVersions] = useState<TrackVersion[]>([]);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<SelectedTrack>();
  const [audioTitle, setAudioTitle] = useState("");
  const [audioKind, setAudioKind] = useState<AudioTrackKind>(
    pageId === "CR-008" ? "bgm" : "dialogue",
  );
  const [subtitleTitle, setSubtitleTitle] = useState("");
  const [language, setLanguage] = useState("zh-CN");
  const [format, setFormat] = useState<SubtitleFormat>("srt");
  const [versionPayload, setVersionPayload] = useState(
    '{"durationMs":1000,"clips":[],"loudnessLufs":-16}',
  );
  const [objectKey, setObjectKey] = useState("");
  const [taskKind, setTaskKind] = useState<MediaTaskKind>(
    pageId === "CR-008"
      ? "mix"
      : pageId === "CR-105"
        ? "subtitle"
        : ["CR-056", "CR-057", "CR-058", "CR-059"].includes(pageId)
          ? "lip_sync"
          : "audio",
  );
  const [taskId, setTaskId] = useState("");
  const [taskResourceType, setTaskResourceType] = useState("audio_track");
  const [taskResourceId, setTaskResourceId] = useState("");
  const [cancelTaskId, setCancelTaskId] = useState("");
  const [cancelVersion, setCancelVersion] = useState("");
  const [cancelReason, setCancelReason] = useState("用户请求取消");
  const [fallbackReason, setFallbackReason] = useState("保留原始版本");
  const [lipAudioVersionId, setLipAudioVersionId] = useState("");
  const [lipSubtitleVersionId, setLipSubtitleVersionId] = useState("");
  const [inputVideoKey, setInputVideoKey] = useState("");
  const [lipCalibration, setLipCalibration] = useState("{}");
  const [lipCheck, setLipCheck] = useState<Record<string, unknown>>();
  const [selectedLipSync, setSelectedLipSync] = useState<LipSyncVersion>();
  const [lipOutputKey, setLipOutputKey] = useState("");
  const [lipSourceTaskId, setLipSourceTaskId] = useState("");
  const pendingKeys = useRef(new Map<string, string>());
  const visibleTasks = page.taskKinds
    ? mediaTasks.filter((task) => page.taskKinds?.has(task.task_kind))
    : mediaTasks;

  const load = useCallback(async () => {
    const controller = new AbortController();
    setState("loading");
    try {
      const [tracksResponse, tasksResponse, lipSyncResponse] =
        await Promise.all([
          api.audioTracks(
            projectId,
            {
              offset: trackPage * 20,
              limit: 20,
              q: appliedTrackQuery,
              trackKind: trackKindFilter,
              state: trackStateFilter,
            },
            controller.signal,
          ),
          api.audioTasks(
            projectId,
            {
              offset: taskPage * 20,
              limit: 20,
              q: appliedTaskQuery,
              status: taskStatusFilter,
              taskKind:
                page.taskKinds?.size === 1
                  ? Array.from(page.taskKinds)[0]
                  : undefined,
            },
            controller.signal,
          ),
          api.lipSyncVersions(projectId, controller.signal),
        ]);
      setAudioTracks(tracksResponse.data.audioTracks);
      setSubtitleTracks(tracksResponse.data.subtitleTracks);
      setMediaTasks(tasksResponse.data.tasks);
      setTrackTotal(tracksResponse.meta.total ?? 0);
      setTaskTotal(tasksResponse.meta.total ?? 0);
      setLipSyncVersions(lipSyncResponse.data.versions);
      setState("ready");
    } catch (error) {
      setState(
        error instanceof XingjingApiError && error.detail.status === 403
          ? "forbidden"
          : "error",
      );
      setMessage(errorText(error));
    }
    return () => controller.abort();
  }, [
    api,
    projectId,
    trackPage,
    taskPage,
    appliedTrackQuery,
    appliedTaskQuery,
    trackKindFilter,
    trackStateFilter,
    taskStatusFilter,
    page.taskKinds,
  ]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const runOnce = useCallback(
    async (operation: string, execute: (key: string) => Promise<unknown>) => {
      if (pendingKeys.current.has(operation)) return;
      const key = createIdempotencyKey(projectId, operation, newId());
      pendingKeys.current.set(operation, key);
      setMessage("");
      try {
        await execute(key);
        await load();
        setMessage("操作已由服务端接受，已刷新最新轨道数据。");
      } catch (error) {
        setMessage(errorText(error));
        if (error instanceof XingjingApiError && error.detail.status === 409)
          await load();
      } finally {
        pendingKeys.current.delete(operation);
      }
    },
    [load, projectId],
  );

  const choose = (next: SelectedTrack) => {
    setSelected(next);
    setTaskResourceId(next.track.id);
    setTaskResourceType(
      next.kind === "audio" ? "audio_track" : "subtitle_track",
    );
    setVersionPayload(
      next.kind === "subtitle"
        ? '{"durationMs":1000,"cues":[]}'
        : next.track.track_kind === "dialogue" ||
            next.track.track_kind === "voiceover"
          ? '{"durationMs":1000,"clips":[],"loudnessLufs":-16}'
          : '{"durationMs":1000,"segments":[],"loudnessLufs":-16}',
    );
    void api
      .trackVersions(projectId, next.kind, next.track.id)
      .then((response) => setTrackVersions(response.data.versions))
      .catch((error) => setMessage(errorText(error)));
  };

  const createAudioTrack = () =>
    void runOnce("create-audio-track", (key) =>
      api.audioAction(
        projectId,
        {
          action: "create_audio_track",
          trackId: newId(),
          trackKind: audioKind,
          title: audioTitle.trim(),
        },
        key,
      ),
    );
  const createSubtitleTrack = () =>
    void runOnce("create-subtitle-track", (key) =>
      api.audioAction(
        projectId,
        {
          action: "create_subtitle_track",
          trackId: newId(),
          language: language.trim(),
          format,
          title: subtitleTitle.trim(),
        },
        key,
      ),
    );
  const appendVersion = () => {
    if (!selected) return;
    try {
      const payload = JSON.parse(versionPayload) as Record<string, unknown>;
      const audio = selected.kind === "audio";
      void runOnce(
        `append-${selected.kind}-version:${selected.track.id}`,
        (key) =>
          api.audioAction(
            projectId,
            audio
              ? {
                  action: "append_audio_version",
                  trackId: selected.track.id,
                  versionId: newId(),
                  cuePayload: payload,
                  objectKey: objectKey.trim(),
                }
              : {
                  action: "append_subtitle_version",
                  trackId: selected.track.id,
                  versionId: newId(),
                  cues: payload,
                  ...(objectKey.trim() ? { objectKey: objectKey.trim() } : {}),
                },
            key,
            selected.track.version,
          ),
      );
    } catch {
      setMessage("版本内容必须是合法 JSON 对象。");
    }
  };
  const requestTask = () =>
    void runOnce(`create-media-task:${taskId}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "create_media_task",
          taskId: taskId.trim(),
          taskKind,
          resourceType: taskResourceType.trim(),
          resourceId: taskResourceId.trim(),
          maxAttempts: 2,
        },
        key,
      ),
    );
  const cancelTask = () =>
    void runOnce(`cancel-media-task:${cancelTaskId}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "cancel_media_task",
          taskId: cancelTaskId.trim(),
          reason: cancelReason.trim(),
        },
        key,
        Number(cancelVersion),
      ),
    );
  const retryTask = (task: MediaTask) =>
    void runOnce(`retry-media-task:${task.id}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "retry_media_task",
          taskId: task.id,
        },
        key,
        task.version,
      ),
    );
  const recordFallback = (task: MediaTask) =>
    void runOnce(`record-media-fallback:${task.id}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "record_media_fallback",
          taskId: task.id,
          reason: fallbackReason.trim(),
        },
        key,
        task.version,
      ),
    );
  const createLipSync = () => {
    try {
      const calibration = JSON.parse(lipCalibration) as Record<string, unknown>;
      void runOnce(`create-lip-sync:${inputVideoKey}`, (key) =>
        api.audioAction(
          projectId,
          {
            action: "create_lip_sync_version",
            versionId: newId(),
            audioVersionId: lipAudioVersionId.trim(),
            ...(lipSubtitleVersionId.trim()
              ? { subtitleVersionId: lipSubtitleVersionId.trim() }
              : {}),
            inputVideoKey: inputVideoKey.trim(),
            calibration,
          },
          key,
        ),
      );
    } catch {
      setMessage("对口型校准参数必须是合法 JSON 对象。");
    }
  };
  const checkLipSync = () => {
    try {
      const calibration = JSON.parse(lipCalibration) as Record<string, unknown>;
      const key = createIdempotencyKey(
        projectId,
        "check-lip-sync",
        newId(),
      );
      setMessage("");
      void api
        .audioAction(
          projectId,
          {
            action: "check_lip_sync_inputs",
            audioVersionId: lipAudioVersionId.trim(),
            ...(lipSubtitleVersionId.trim()
              ? { subtitleVersionId: lipSubtitleVersionId.trim() }
              : {}),
            inputVideoKey: inputVideoKey.trim(),
            calibration,
          },
          key,
        )
        .then((response) => {
          const result = response.data.result;
          setLipCheck(
            typeof result === "object" && result !== null
              ? (result as Record<string, unknown>)
              : undefined,
          );
          setMessage(
            typeof result === "object" &&
              result !== null &&
              "allowed" in result &&
              result.allowed === true
              ? "对口型检测通过，可以创建候选。"
              : "对口型检测未通过，请根据检查项调整输入。",
          );
        })
        .catch((error) => setMessage(errorText(error)));
    } catch {
      setMessage("对口型校准参数必须是合法 JSON 对象。");
    }
  };
  const chooseLipSync = (version: LipSyncVersion) => {
    setSelectedLipSync(version);
    setTaskKind("lip_sync");
    setTaskResourceType("lip_sync_version");
    setTaskResourceId(version.id);
  };
  const completeLipSync = () => {
    if (!selectedLipSync) return;
    void runOnce(`complete-lip-sync:${selectedLipSync.id}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "complete_lip_sync_version",
          versionId: selectedLipSync.id,
          sourceTaskId: lipSourceTaskId.trim(),
          outputKey: lipOutputKey.trim(),
        },
        key,
        selectedLipSync.version,
      ),
    );
  };
  const selectLipSync = (version: LipSyncVersion) =>
    void runOnce(`select-lip-sync:${version.id}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "select_lip_sync_version",
          versionId: version.id,
        },
        key,
        version.version,
      ),
    );
  const fallbackLipSync = (version: LipSyncVersion) =>
    void runOnce(`fallback-lip-sync:${version.id}`, (key) =>
      api.audioAction(
        projectId,
        {
          action: "create_lip_sync_fallback",
          versionId: newId(),
          fallbackOfId: version.id,
        },
        key,
      ),
    );

  if (state === "loading")
    return (
      <main
        aria-busy="true"
        aria-label="音频字幕工作区"
        className="min-h-full bg-slate-950 p-6 text-slate-100"
      >
        <p>正在读取服务端音频与字幕轨…</p>
      </main>
    );
  if (state === "forbidden")
    return (
      <main role="alert" className="min-h-full bg-slate-950 p-6 text-slate-100">
        <h1>无权访问音频字幕</h1>
        <p>{message}</p>
      </main>
    );
  if (state === "error")
    return (
      <main role="alert" className="min-h-full bg-slate-950 p-6 text-slate-100">
        <h1>音频字幕加载失败</h1>
        <p>{message}</p>
        <button onClick={() => void load()}>重新加载</button>
      </main>
    );

  return (
    <main className="min-h-full bg-slate-950 p-6 text-slate-100">
      <header className="border-b border-slate-700 pb-5">
        <p className="text-sm text-cyan-300">
          项目 / {projectName ?? projectId} / {pageId}
        </p>
        <h1 className="text-2xl font-semibold">{page.title}</h1>
        <p className="mt-1 text-sm text-slate-400">
          {page.description} 展示内容均来自服务端；媒体任务仅在已配置 Provider
          时提交。
        </p>
      </header>
      {message ? (
        <p
          role="alert"
          className="my-4 rounded border border-amber-700 bg-amber-950 p-3"
        >
          {message}
        </p>
      ) : null}

      <section
        className={`mt-6 grid gap-4 lg:grid-cols-2 ${page.sections.has("create") ? "" : "hidden"}`}
        aria-label="创建轨道"
      >
        <form
          className="rounded border border-slate-700 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (audioTitle.trim()) createAudioTrack();
          }}
        >
          <h2>创建音频轨</h2>
          <label className="block mt-3">
            名称
            <input
              className="block w-full"
              value={audioTitle}
              onChange={(event) => setAudioTitle(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            类型
            <select
              className="block w-full"
              value={audioKind}
              onChange={(event) =>
                setAudioKind(event.target.value as AudioTrackKind)
              }
            >
              {(["dialogue", "voiceover", "bgm", "sfx", "mix"] as const).map(
                (value) => (
                  <option key={value}>{value}</option>
                ),
              )}
            </select>
          </label>
          <button className="mt-4" type="submit">
            创建音频轨
          </button>
        </form>
        <form
          className="rounded border border-slate-700 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (subtitleTitle.trim() && language.trim()) createSubtitleTrack();
          }}
        >
          <h2>创建字幕轨</h2>
          <label className="block mt-3">
            名称
            <input
              className="block w-full"
              value={subtitleTitle}
              onChange={(event) => setSubtitleTitle(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            语言
            <input
              className="block w-full"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            格式
            <select
              className="block w-full"
              value={format}
              onChange={(event) =>
                setFormat(event.target.value as SubtitleFormat)
              }
            >
              {(["srt", "ass", "vtt", "native"] as const).map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <button className="mt-4" type="submit">
            创建字幕轨
          </button>
        </form>
      </section>

      <section
        className={`mt-6 ${page.sections.has("tracks") ? "" : "hidden"}`}
        aria-label="服务端轨道"
      >
        <h2>服务端轨道</h2>
        <form
          className="mt-3 grid gap-3 md:grid-cols-4"
          onSubmit={(event) => {
            event.preventDefault();
            setTrackPage(0);
            setAppliedTrackQuery(trackQuery.trim());
          }}
        >
          <label>
            搜索 ID/名称
            <input
              className="block w-full"
              value={trackQuery}
              onChange={(event) => setTrackQuery(event.target.value)}
            />
          </label>
          <label>
            音频类型
            <select
              className="block w-full"
              value={trackKindFilter}
              onChange={(event) => {
                setTrackPage(0);
                setTrackKindFilter(event.target.value);
              }}
            >
              <option value="">全部</option>
              {(["dialogue", "voiceover", "bgm", "sfx", "mix"] as const).map(
                (value) => (
                  <option key={value}>{value}</option>
                ),
              )}
            </select>
          </label>
          <label>
            状态
            <select
              className="block w-full"
              value={trackStateFilter}
              onChange={(event) => {
                setTrackPage(0);
                setTrackStateFilter(event.target.value);
              }}
            >
              <option value="">全部</option>
              {(["draft", "ready", "archived"] as const).map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <button className="self-end" type="submit">
            查询
          </button>
        </form>
        {audioTracks.length + subtitleTracks.length === 0 ? (
          <p className="mt-3 text-slate-400">
            此项目尚无音频或字幕轨。请先创建一条轨道。
          </p>
        ) : (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {audioTracks.map((track) => (
              <button
                type="button"
                key={track.id}
                onClick={() => choose({ kind: "audio", track })}
                className="rounded border border-slate-700 p-4 text-left"
              >
                <strong>{track.title}</strong>
                <p>
                  音频 · {track.track_kind} · {track.state}
                </p>
                <p className="text-sm text-slate-400">
                  ID {track.id} · 版本 {track.version} · 修订{" "}
                  {track.current_revision} · 更新 {timestamp(track.updated_at)}
                </p>
              </button>
            ))}
            {subtitleTracks.map((track) => (
              <button
                type="button"
                key={track.id}
                onClick={() => choose({ kind: "subtitle", track })}
                className="rounded border border-slate-700 p-4 text-left"
              >
                <strong>{track.title}</strong>
                <p>
                  字幕 · {track.language} / {track.format} · {track.state}
                </p>
                <p className="text-sm text-slate-400">
                  ID {track.id} · 版本 {track.version} · 修订{" "}
                  {track.current_revision} · 更新 {timestamp(track.updated_at)}
                </p>
              </button>
            ))}
          </div>
        )}
        <div className="mt-4 flex items-center justify-between">
          <span className="text-sm text-slate-400">共 {trackTotal} 条</span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={trackPage === 0}
              onClick={() => setTrackPage((value) => Math.max(0, value - 1))}
            >
              上一页
            </button>
            <button
              type="button"
              disabled={(trackPage + 1) * 20 >= trackTotal}
              onClick={() => setTrackPage((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </section>

      <section
        className={`mt-6 grid gap-4 lg:grid-cols-2 ${page.sections.has("lip-create") ? "" : "hidden"}`}
        aria-label="对口型版本"
      >
        <form
          className="rounded border border-slate-700 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (lipAudioVersionId.trim() && inputVideoKey.trim())
              createLipSync();
          }}
        >
          <h2>创建对口型候选</h2>
          <p className="mt-1 text-sm text-slate-400">
            选择轨道后会显示其不可变版本；输入视频必须是本项目 M06
            已保存的视频对象。
          </p>
          <p className="mt-3 text-sm text-cyan-300">
            {selected
              ? `${selected.track.title} 的版本：${trackVersions.map((item) => `${item.id} (r${item.revision})`).join(" · ") || "暂无版本"}`
              : "先选择音频或字幕轨以查看版本"}
          </p>
          <label className="block mt-3">
            音频版本 ID
            <input
              className="block w-full"
              value={lipAudioVersionId}
              onChange={(event) => setLipAudioVersionId(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            字幕版本 ID（可选）
            <input
              className="block w-full"
              value={lipSubtitleVersionId}
              onChange={(event) => setLipSubtitleVersionId(event.target.value)}
            />
          </label>
          <label className="block mt-3">
            输入视频对象 key
            <input
              className="block w-full"
              value={inputVideoKey}
              onChange={(event) => setInputVideoKey(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            脸部/时间校准 JSON
            <textarea
              className="block min-h-24 w-full"
              value={lipCalibration}
              onChange={(event) => setLipCalibration(event.target.value)}
              required
            />
          </label>
          {lipCheck ? (
            <pre className="mt-3 max-h-48 overflow-auto rounded bg-slate-900 p-3 text-xs">
              {JSON.stringify(lipCheck, null, 2)}
            </pre>
          ) : null}
          <button
            className="mt-4 mr-3"
            type="button"
            onClick={checkLipSync}
            disabled={!lipAudioVersionId.trim() || !inputVideoKey.trim()}
          >
            执行真实检测
          </button>
          <button className="mt-4" type="submit">
            创建候选
          </button>
        </form>
        <form
          className={`rounded border border-slate-700 p-4 ${pageId === "CR-056" ? "hidden" : ""}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (
              selectedLipSync &&
              lipSourceTaskId.trim() &&
              lipOutputKey.trim()
            )
              completeLipSync();
          }}
        >
          <h2>保存对口型结果</h2>
          <p className="mt-1 text-sm text-slate-400">
            Provider 成功回调已将产物对象 key
            写入任务结果。选择对应候选后，用该任务和对象 key 保存不可变版本。
          </p>
          <p className="mt-3 text-sm text-cyan-300">
            {selectedLipSync
              ? `候选 ${selectedLipSync.id}（版本 ${selectedLipSync.version}，状态 ${selectedLipSync.state}）`
              : "先从下方版本列表选择候选"}
          </p>
          <label className="block mt-3">
            来源任务 ID
            <input
              className="block w-full"
              value={lipSourceTaskId}
              onChange={(event) => setLipSourceTaskId(event.target.value)}
              disabled={!selectedLipSync}
              required
            />
          </label>
          <label className="block mt-3">
            输出视频对象 key
            <input
              className="block w-full"
              value={lipOutputKey}
              onChange={(event) => setLipOutputKey(event.target.value)}
              disabled={!selectedLipSync}
              required
            />
          </label>
          <button
            className="mt-4"
            type="submit"
            disabled={!selectedLipSync || selectedLipSync.state === "ready"}
          >
            保存候选版本
          </button>
        </form>
      </section>

      <section
        className={`mt-6 ${page.sections.has("lip-compare") ? "" : "hidden"}`}
        aria-label="对口型版本对比"
      >
        <h2>对口型版本对比</h2>
        <p className="mt-1 text-sm text-slate-400">
          同一输入视频最多只有一个服务端确认的保留版本；选择“保留原视频”会生成可审计的回退候选。比较预览默认静音，字幕和音频仍以已选择的不可变轨道版本为准。
        </p>
        {lipSyncVersions.length === 0 ? (
          <p className="mt-3 text-slate-400">暂无对口型候选版本。</p>
        ) : (
          <div className="mt-3 grid gap-3">
            {lipSyncVersions.map((version) => (
              <article
                key={version.id}
                className="rounded border border-slate-700 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <button
                    type="button"
                    className="text-left"
                    onClick={() => chooseLipSync(version)}
                  >
                    <strong>{version.id}</strong>
                    <p className="text-sm text-slate-400">
                      输入 {version.input_video_key} · 音频{" "}
                      {version.audio_version_id} · 字幕{" "}
                      {version.subtitle_version_id ?? "无"}
                    </p>
                    <p className="text-sm text-slate-400">
                      状态 {version.state} · 版本 {version.version} ·{" "}
                      {version.is_selected ? "已保留" : "未保留"} · 更新{" "}
                      {timestamp(version.updated_at)}
                    </p>
                    {version.output_video_key ? (
                      <p className="text-sm text-emerald-300">
                        候选视频 {version.output_video_key}
                      </p>
                    ) : null}
                  </button>
                  <div className="flex gap-2">
                    {["ready", "fallback"].includes(version.state) ? (
                      <button
                        type="button"
                        onClick={() => selectLipSync(version)}
                        disabled={version.is_selected}
                      >
                        保留此版本
                      </button>
                    ) : null}
                    {version.state === "ready" ? (
                      <button
                        type="button"
                        onClick={() => fallbackLipSync(version)}
                      >
                        保留原视频
                      </button>
                    ) : null}
                  </div>
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                  <div>
                    <p className="mb-2 text-sm text-slate-300">原始视频</p>
                    {version.input_asset_id ? (
                      <video
                        className="w-full rounded bg-black"
                        controls
                        muted
                        preload="metadata"
                        src={api.generatedAssetUrl(
                          projectId,
                          version.input_asset_id,
                        )}
                      >
                        浏览器不支持视频预览。
                      </video>
                    ) : (
                      <p className="text-sm text-amber-300">
                        原视频资产已不可用，无法预览。
                      </p>
                    )}
                  </div>
                  <div>
                    <p className="mb-2 text-sm text-slate-300">对口型候选</p>
                    {version.output_video_key ? (
                      <video
                        className="w-full rounded bg-black"
                        controls
                        muted
                        preload="metadata"
                        src={api.audioObjectUrl(
                          projectId,
                          version.output_video_key,
                        )}
                      >
                        浏览器不支持视频预览。
                      </video>
                    ) : (
                      <p className="text-sm text-slate-400">
                        候选尚未完成，暂无视频。
                      </p>
                    )}
                  </div>
                </div>
                {version.failure_metadata ? (
                  <p className="mt-2 text-sm text-amber-300">
                    失败信息：{JSON.stringify(version.failure_metadata)}
                  </p>
                ) : null}
                {version.fallback_of_id ? (
                  <p className="mt-2 text-sm text-cyan-300">
                    回退自候选：{version.fallback_of_id}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </section>

      <section
        className="mt-6 grid gap-4 lg:grid-cols-2"
        aria-label="受约束写入"
      >
        <form
          className={`rounded border border-slate-700 p-4 ${page.sections.has("version") ? "" : "hidden"}`}
          onSubmit={(event) => {
            event.preventDefault();
            appendVersion();
          }}
        >
          <h2>追加 {selected?.kind === "subtitle" ? "字幕" : "音频"}版本</h2>
          <p className="mt-1 text-sm text-slate-400">
            {selected
              ? `${selected.track.title}（If-Match: ${selected.track.version}）`
              : "先从服务端轨道中选择一项"}
          </p>
          <label className="block mt-3">
            JSON 内容
            <textarea
              className="block min-h-28 w-full"
              value={versionPayload}
              onChange={(event) => setVersionPayload(event.target.value)}
              disabled={!selected}
            />
          </label>
          <label className="block mt-3">
            对象存储 key{selected?.kind === "audio" ? "（必填）" : "（可选）"}
            <input
              className="block w-full"
              value={objectKey}
              onChange={(event) => setObjectKey(event.target.value)}
              disabled={!selected}
              required={selected?.kind === "audio"}
            />
          </label>
          <button className="mt-4" type="submit" disabled={!selected}>
            追加版本
          </button>
        </form>
        <form
          className={`rounded border border-slate-700 p-4 ${page.sections.has("task-create") ? "" : "hidden"}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (
              taskId.trim() &&
              taskResourceType.trim() &&
              taskResourceId.trim()
            )
              requestTask();
          }}
        >
          <h2>请求媒体任务</h2>
          <p className="mt-1 text-sm text-slate-400">
            不生成本地占位媒体；Provider
            未配置时会显示服务端实际失败原因。每项任务默认最多尝试两次。
          </p>
          <label className="block mt-3">
            任务 ID
            <input
              className="block w-full"
              value={taskId}
              onChange={(event) => setTaskId(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            任务类型
            <select
              className="block w-full"
              value={taskKind}
              onChange={(event) =>
                setTaskKind(event.target.value as MediaTaskKind)
              }
            >
              {(["audio", "subtitle", "lip_sync", "mix"] as const).map(
                (value) => (
                  <option key={value}>{value}</option>
                ),
              )}
            </select>
          </label>
          <label className="block mt-3">
            资源类型
            <input
              className="block w-full"
              value={taskResourceType}
              onChange={(event) => setTaskResourceType(event.target.value)}
              required
            />
          </label>
          <label className="block mt-3">
            资源 ID
            <input
              className="block w-full"
              value={taskResourceId}
              onChange={(event) => setTaskResourceId(event.target.value)}
              required
            />
          </label>
          <button className="mt-4" type="submit">
            提交媒体任务
          </button>
        </form>
        <form
          className={`rounded border border-slate-700 p-4 lg:col-span-2 ${page.sections.has("task-cancel") ? "" : "hidden"}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (
              cancelTaskId.trim() &&
              Number.isInteger(Number(cancelVersion)) &&
              Number(cancelVersion) > 0 &&
              cancelReason.trim()
            )
              cancelTask();
          }}
        >
          <h2>取消媒体任务</h2>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <label>
              任务 ID
              <input
                className="block w-full"
                value={cancelTaskId}
                onChange={(event) => setCancelTaskId(event.target.value)}
                required
              />
            </label>
            <label>
              任务版本
              <input
                className="block w-full"
                type="number"
                min="1"
                value={cancelVersion}
                onChange={(event) => setCancelVersion(event.target.value)}
                required
              />
            </label>
            <label>
              取消原因
              <input
                className="block w-full"
                value={cancelReason}
                onChange={(event) => setCancelReason(event.target.value)}
                required
              />
            </label>
          </div>
          <button className="mt-4" type="submit">
            取消任务
          </button>
        </form>
      </section>
      <section
        className={`mt-6 ${page.sections.has("tasks") ? "" : "hidden"}`}
        aria-label="服务端媒体任务"
      >
        <h2>服务端媒体任务</h2>
        <p className="mt-1 text-sm text-slate-400">
          失败任务可在剩余尝试次数内重试；选择保留原始版本会写入服务端审计记录。
        </p>
        <form
          className="mt-3 grid gap-3 md:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTaskPage(0);
            setAppliedTaskQuery(taskQuery.trim());
          }}
        >
          <label>
            搜索任务/资源/Provider ID
            <input
              className="block w-full"
              value={taskQuery}
              onChange={(event) => setTaskQuery(event.target.value)}
            />
          </label>
          <label>
            状态
            <select
              className="block w-full"
              value={taskStatusFilter}
              onChange={(event) => {
                setTaskPage(0);
                setTaskStatusFilter(event.target.value);
              }}
            >
              <option value="">全部</option>
              {[
                "pending",
                "queued",
                "running",
                "cancelling",
                "retrying",
                "succeeded",
                "failed",
                "cancelled",
                "timed_out",
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <button className="self-end" type="submit">
            查询
          </button>
        </form>
        <label className="mt-3 block max-w-xl">
          回退原因
          <input
            className="block w-full"
            value={fallbackReason}
            onChange={(event) => setFallbackReason(event.target.value)}
            required
          />
        </label>
        {visibleTasks.length === 0 ? (
          <p className="mt-3 text-slate-400">暂无媒体任务。</p>
        ) : (
          <div className="mt-3 grid gap-3">
            {visibleTasks.map((task) => (
              <article
                key={task.id}
                className="rounded border border-slate-700 p-4"
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <strong>{task.task_kind}</strong>
                    <p className="text-sm text-slate-400">
                      任务 {task.id} · 资源 {task.resource_type}/
                      {task.resource_id} · 状态 {task.status} · 版本{" "}
                      {task.version}
                    </p>
                    <p className="text-sm text-slate-400">
                      尝试 {task.attempt_count ?? 0}/{task.max_attempts ?? 1} ·
                      更新 {timestamp(task.updated_at)}
                    </p>
                    <p className="text-sm text-slate-400">
                      费用 {task.billing_status ?? "未知"} · 预估{" "}
                      {task.estimated_minor ?? 0} {task.billing_currency ?? ""} ·
                      实扣 {task.actual_minor ?? 0} · 退回{" "}
                      {task.released_minor ?? 0} · 计价版本{" "}
                      {task.pricing_version ?? "—"}
                    </p>
                  </div>
                  {["failed", "timed_out"].includes(task.status) ? (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => retryTask(task)}
                        disabled={
                          (task.attempt_count ?? 0) >=
                            (task.max_attempts ?? 1) ||
                          task.failure_metadata?.retryable === false
                        }
                      >
                        重试
                      </button>
                      <button
                        type="button"
                        onClick={() => recordFallback(task)}
                        disabled={!fallbackReason.trim()}
                      >
                        保留原版本
                      </button>
                    </div>
                  ) : null}
                </div>
                {task.failure_metadata ? (
                  <p className="mt-2 text-sm text-amber-300">
                    失败信息：{JSON.stringify(task.failure_metadata)}
                  </p>
                ) : null}
                {task.fallback_metadata ? (
                  <p className="mt-2 text-sm text-cyan-300">
                    回退：{JSON.stringify(task.fallback_metadata)}
                  </p>
                ) : null}
                {task.result_metadata ? (
                  <p className="mt-2 text-sm text-emerald-300">
                    生成产物：{JSON.stringify(task.result_metadata)}
                  </p>
                ) : null}
              </article>
            ))}
          </div>
        )}
        <div className="mt-4 flex items-center justify-between">
          <span className="text-sm text-slate-400">共 {taskTotal} 条</span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={taskPage === 0}
              onClick={() => setTaskPage((value) => Math.max(0, value - 1))}
            >
              上一页
            </button>
            <button
              type="button"
              disabled={(taskPage + 1) * 20 >= taskTotal}
              onClick={() => setTaskPage((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
