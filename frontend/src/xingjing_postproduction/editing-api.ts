export interface EditingPreview {
  readonly timeline_id: string;
  readonly project_id: string;
  readonly episode_id: string;
  readonly final_video_id: string;
  readonly timeline_version_id: string;
  readonly timeline_revision: number;
  readonly composition_sha256: string;
  readonly input_snapshot_sha256: string;
  readonly duration_ms: number;
  readonly track_count: number;
  readonly clip_count: number;
  readonly source_version_ids: readonly string[];
  readonly updated_at: string;
}

export interface EditingSourceVersion {
  readonly asset_id: string;
  readonly version_id: string;
  readonly duration_ms: number;
  readonly content_sha256: string;
  readonly media_kind: "video" | "voice" | "subtitle" | "bgm" | "sfx";
}

export interface EditingClip {
  readonly clip_id: string;
  readonly source_version_id: string;
  readonly source_in_ms: number;
  readonly source_out_ms: number;
  readonly timeline_start_ms: number;
  readonly timeline_end_ms: number;
  readonly volume_milli: number;
  readonly effects: Readonly<Record<string, unknown>>;
}

export interface EditingTimelineTrack {
  readonly track_id: string;
  readonly kind:
    | "video"
    | "voice"
    | "subtitle"
    | "bgm"
    | "sfx"
    | "watermark"
    | "opening"
    | "ending"
    | "review_marker";
  readonly clips: readonly EditingClip[];
}

export interface EditingTimelineSnapshot {
  readonly timeline_id: string;
  readonly version_id: string;
  readonly project_id: string;
  readonly final_video_id: string;
  readonly episode_id: string;
  readonly revision: number;
  readonly tracks: readonly EditingTimelineTrack[];
  readonly output_policy: EditingOutputPolicy;
}

export interface EditingOutputPolicy {
  readonly platform_watermark_enabled: boolean;
  readonly customer_watermark_text: string;
  readonly watermark_position:
    | "top_left"
    | "top_right"
    | "bottom_left"
    | "bottom_right"
    | "center";
  readonly watermark_opacity_milli: number;
  readonly aigc_label_enabled: boolean;
  readonly aigc_label_style:
    | "visible"
    | "metadata"
    | "visible_and_metadata";
  readonly export_template: "preview" | "review" | "delivery" | "platform";
}

export interface CreateTimelineInput {
  readonly episodeId: string;
  readonly sourceVersionId: string;
  readonly sourceOutMs: number;
}

export interface ReplaceClipInput {
  readonly trackId: string;
  readonly clipId: string;
  readonly sourceVersionId: string;
  readonly sourceInMs: number;
  readonly sourceOutMs: number;
  readonly expectedRevision: number;
}

export interface UpdateClipInput {
  readonly trackId: string;
  readonly clipId: string;
  readonly sourceInMs: number;
  readonly sourceOutMs: number;
  readonly timelineStartMs: number;
  readonly timelineEndMs: number;
  readonly volumeMilli: number;
  readonly effects: Readonly<Record<string, string | number | boolean | null>>;
  readonly expectedRevision: number;
}

export interface EditingRenderTask {
  readonly task_id: string;
  readonly timeline_id: string;
  readonly timeline_version_id: string;
  readonly timeline_revision: number;
  readonly status:
    | "queued"
    | "running"
    | "retrying"
    | "cancelling"
    | "cancelled"
    | "failed"
    | "succeeded";
  readonly attempt: number;
  readonly max_attempts: number;
  readonly task_revision: number;
  readonly renderer_job_id: string | null;
  readonly failure: {
    readonly code: string;
    readonly message: string;
    readonly retryable: boolean;
  } | null;
  readonly output_version_id: string | null;
  readonly billing_currency: string;
  readonly billing_estimated_minor: number;
  readonly billing_actual_minor: number | null;
  readonly billing_pricing_version: string;
  readonly billing_status: "active" | "settled" | "released";
  readonly created_at: string;
  readonly updated_at: string;
  readonly deadline_at: string;
}

export interface EditingFinalVideoVersion {
  readonly version_id: string;
  readonly final_video_id: string;
  readonly timeline_id: string;
  readonly timeline_version_id: string;
  readonly render_task_id: string;
  readonly render_attempt: number;
  readonly profile: RenderProfileInput;
  readonly preview: EditingPreview;
  readonly output: {
    readonly object_key: string;
    readonly content_sha256: string;
    readonly size_bytes: number;
    readonly duration_ms: number;
    readonly input_snapshot_sha256: string;
    readonly composition_sha256: string;
  };
  readonly created_at: string;
}

export interface EditingFinalVideoSelection {
  readonly project_id: string;
  readonly final_video_id: string;
  readonly selected_version_id: string;
  readonly revision: number;
  readonly selected_by: string;
  readonly selected_at: string;
}

export interface RenderProfileInput {
  readonly container: "mp4" | "mov";
  readonly video_codec: "h264" | "h265";
  readonly audio_codec: "aac";
  readonly width: number;
  readonly height: number;
  readonly frame_rate_milli: number;
}

export interface SubmitRenderInput {
  readonly preview: EditingPreview;
  readonly profile: RenderProfileInput;
  readonly deadlineAt: string;
  readonly maxAttempts: number;
}

export class EditingApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "EditingApiError";
  }
}

export interface EditingApiOptions {
  readonly fetcher?: typeof fetch;
  readonly getAccessToken?: () => string | null | Promise<string | null>;
  readonly createIdempotencyKey?: () => string;
}

function errorPayload(payload: unknown) {
  const detail =
    typeof payload === "object" && payload !== null && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : undefined;
  if (typeof detail === "object" && detail !== null) {
    const value = detail as { code?: unknown; message?: unknown };
    return {
      code:
        typeof value.code === "string" ? value.code : "EDITING_REQUEST_FAILED",
      message:
        typeof value.message === "string"
          ? value.message
          : "成片服务未返回可用结果",
    };
  }
  return { code: "EDITING_REQUEST_FAILED", message: "成片服务未返回可用结果" };
}

export function createEditingApi({
  fetcher = fetch,
  getAccessToken,
  createIdempotencyKey = () => crypto.randomUUID(),
}: EditingApiOptions = {}) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    const token = await getAccessToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let response: Response;
    try {
      response = await fetcher(`/api/v1${path}`, {
        ...init,
        headers,
        credentials: "same-origin",
      });
    } catch {
      throw new EditingApiError(
        0,
        "NETWORK_ERROR",
        "无法连接成片服务，请检查网络后重试。",
      );
    }
    const payload: unknown = await response.json().then(
      (body: unknown) => body,
      () => null,
    );
    if (!response.ok) {
      const error = errorPayload(payload);
      throw new EditingApiError(response.status, error.code, error.message);
    }
    return payload as T;
  }

  const projectPath = (projectId: string) =>
    `/projects/${encodeURIComponent(projectId)}`;
  return {
    listTimelines: (
      projectId: string,
      options: { offset?: number; limit?: number; episodeId?: string } = {},
    ) => {
      const params = new URLSearchParams({
        offset: String(options.offset ?? 0),
        limit: String(options.limit ?? 20),
      });
      if (options.episodeId) params.set("episode_id", options.episodeId);
      return request<EditingPreview[]>(
        `${projectPath(projectId)}/timelines?${params.toString()}`,
      );
    },
    listSources: (projectId: string) =>
      request<EditingSourceVersion[]>(
        `${projectPath(projectId)}/timeline-sources`,
      ),
    listRenderTasks: (projectId: string) =>
      request<EditingRenderTask[]>(`${projectPath(projectId)}/render-tasks`),
    listFinalVideoVersions: (projectId: string, finalVideoId?: string) =>
      request<EditingFinalVideoVersion[]>(
        `${projectPath(projectId)}/final-video-versions${
          finalVideoId
            ? `?final_video_id=${encodeURIComponent(finalVideoId)}`
            : ""
        }`,
      ),
    getFinalVideoSelection: (projectId: string, finalVideoId: string) =>
      request<EditingFinalVideoSelection | null>(
        `${projectPath(projectId)}/final-videos/${encodeURIComponent(finalVideoId)}/selection`,
      ),
    selectFinalVideoVersion: (
      projectId: string,
      finalVideoId: string,
      versionId: string,
      expectedRevision: number,
    ) =>
      request<EditingFinalVideoSelection>(
        `${projectPath(projectId)}/final-videos/${encodeURIComponent(finalVideoId)}/selection`,
        {
          method: "PUT",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            version_id: versionId,
            expected_revision: expectedRevision,
          }),
        },
      ),
    getPreview: (projectId: string, timelineId: string) =>
      request<EditingPreview>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}`,
      ),
    getTimeline: (projectId: string, timelineId: string) =>
      request<EditingTimelineSnapshot>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}/snapshot`,
      ),
    createTimeline: (projectId: string, input: CreateTimelineInput) =>
      request<EditingPreview>(`${projectPath(projectId)}/timelines`, {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({
          episode_id: input.episodeId,
          tracks: [
            {
              track_id: `video-${createIdempotencyKey()}`,
              kind: "video",
              clips: [
                {
                  clip_id: `clip-${createIdempotencyKey()}`,
                  source_version_id: input.sourceVersionId,
                  source_in_ms: 0,
                  source_out_ms: input.sourceOutMs,
                  timeline_start_ms: 0,
                  timeline_end_ms: input.sourceOutMs,
                },
              ],
            },
          ],
        }),
      }),
    replaceClip: (
      projectId: string,
      timelineId: string,
      input: ReplaceClipInput,
    ) =>
      request<EditingPreview>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}/clips/replace`,
        {
          method: "POST",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            track_id: input.trackId,
            clip_id: input.clipId,
            source_version_id: input.sourceVersionId,
            source_in_ms: input.sourceInMs,
            source_out_ms: input.sourceOutMs,
            expected_revision: input.expectedRevision,
          }),
        },
      ),
    updateClip: (
      projectId: string,
      timelineId: string,
      input: UpdateClipInput,
    ) =>
      request<EditingPreview>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}/clips/${encodeURIComponent(input.clipId)}`,
        {
          method: "PUT",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            track_id: input.trackId,
            clip_id: input.clipId,
            source_in_ms: input.sourceInMs,
            source_out_ms: input.sourceOutMs,
            timeline_start_ms: input.timelineStartMs,
            timeline_end_ms: input.timelineEndMs,
            volume_milli: input.volumeMilli,
            effects: input.effects,
            expected_revision: input.expectedRevision,
          }),
        },
      ),
    updateOutputPolicy: (
      projectId: string,
      timelineId: string,
      expectedRevision: number,
      policy: EditingOutputPolicy,
    ) =>
      request<EditingTimelineSnapshot>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}/output-policy`,
        {
          method: "PUT",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            expected_revision: expectedRevision,
            policy,
          }),
        },
      ),
    addTrack: (
      projectId: string,
      timelineId: string,
      expectedRevision: number,
      source: EditingSourceVersion,
    ) =>
      request<EditingTimelineSnapshot>(
        `${projectPath(projectId)}/timelines/${encodeURIComponent(timelineId)}/tracks`,
        {
          method: "POST",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            expected_revision: expectedRevision,
            track: {
              track_id: `${source.media_kind}-${createIdempotencyKey()}`,
              kind: source.media_kind,
              clips: [
                {
                  clip_id: `clip-${createIdempotencyKey()}`,
                  source_version_id: source.version_id,
                  source_in_ms: 0,
                  source_out_ms: source.duration_ms,
                  timeline_start_ms: 0,
                  timeline_end_ms: source.duration_ms,
                  volume_milli: 1000,
                  effects: {},
                },
              ],
            },
          }),
        },
      ),
    getRenderTask: (projectId: string, taskId: string) =>
      request<EditingRenderTask>(
        `${projectPath(projectId)}/render-tasks/${encodeURIComponent(taskId)}`,
      ),
    cancelRender: (
      projectId: string,
      taskId: string,
      expectedTaskRevision: number,
      reason: string,
    ) =>
      request<EditingRenderTask>(
        `${projectPath(projectId)}/render-tasks/${encodeURIComponent(taskId)}/cancel`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_task_revision: expectedTaskRevision,
            reason,
          }),
        },
      ),
    retryRender: (
      projectId: string,
      taskId: string,
      expectedTaskRevision: number,
    ) =>
      request<EditingRenderTask>(
        `${projectPath(projectId)}/render-tasks/${encodeURIComponent(taskId)}/retry`,
        {
          method: "POST",
          headers: { "Idempotency-Key": createIdempotencyKey() },
          body: JSON.stringify({
            expected_task_revision: expectedTaskRevision,
          }),
        },
      ),
    submitRender: (projectId: string, input: SubmitRenderInput) =>
      request<EditingRenderTask>(`${projectPath(projectId)}/render-tasks`, {
        method: "POST",
        headers: { "Idempotency-Key": createIdempotencyKey() },
        body: JSON.stringify({
          timeline_id: input.preview.timeline_id,
          timeline_version_id: input.preview.timeline_version_id,
          expected_timeline_revision: input.preview.timeline_revision,
          profile: input.profile,
          deadline_at: input.deadlineAt,
          max_attempts: input.maxAttempts,
        }),
      }),
  };
}

export type EditingApi = ReturnType<typeof createEditingApi>;
