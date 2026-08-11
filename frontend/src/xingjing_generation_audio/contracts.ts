export type AudioTrackKind = "dialogue" | "voiceover" | "bgm" | "sfx" | "mix";
export type SubtitleFormat = "srt" | "ass" | "vtt" | "native";
export type TrackState = "draft" | "ready" | "archived";
export type MediaTaskKind = "audio" | "subtitle" | "lip_sync" | "mix";

export interface AudioTrack {
  id: string;
  track_kind: AudioTrackKind;
  title: string;
  state: TrackState;
  current_revision: number;
  version: number;
  updated_at?: string;
}

export interface SubtitleTrack {
  id: string;
  language: string;
  format: SubtitleFormat;
  title: string;
  state: TrackState;
  current_revision: number;
  version: number;
  updated_at?: string;
}

export interface MediaTask {
  id: string;
  task_kind: MediaTaskKind;
  resource_type: string;
  resource_id: string;
  status: string;
  version: number;
  attempt_count?: number;
  max_attempts?: number;
  failure_metadata?: Record<string, unknown> | null;
  fallback_metadata?: Record<string, unknown> | null;
  result_metadata?: Record<string, unknown> | null;
  billing_currency?: string;
  estimated_minor?: number;
  actual_minor?: number;
  released_minor?: number;
  pricing_version?: string;
  billing_status?: "active" | "settled" | "released";
  updated_at?: string;
}

export interface AudioTracksData {
  audioTracks: AudioTrack[];
  subtitleTracks: SubtitleTrack[];
}

export interface AudioTasksData { tasks: MediaTask[] }

export interface TrackVersion {
  id: string;
  track_id: string;
  revision: number;
  object_key?: string | null;
  created_at?: string;
}

export interface LipSyncVersion {
  id: string;
  audio_version_id: string;
  subtitle_version_id?: string | null;
  input_video_key: string;
  input_asset_id?: string | null;
  output_video_key?: string | null;
  state: "pending" | "ready" | "failed" | "cancelled" | "fallback";
  version: number;
  is_selected: boolean;
  fallback_of_id?: string | null;
  failure_metadata?: Record<string, unknown> | null;
  calibration: Record<string, unknown>;
  updated_at?: string;
}

export interface TrackVersionsData { versions: TrackVersion[] }
export interface LipSyncVersionsData { versions: LipSyncVersion[] }

export interface ApiMeta { requestId?: string; total?: number }
export interface ApiEnvelope<T> { data: T; meta: ApiMeta }
export interface ApiFailure { status: number; code: string; message: string; requestId?: string }

export interface AudioActionResult { result: AudioTrack | SubtitleTrack | MediaTask | Record<string, unknown> }

export function createIdempotencyKey(projectId: string, action: string, submissionId: string) {
  return `xj:${projectId}:${action}:${submissionId}`;
}
