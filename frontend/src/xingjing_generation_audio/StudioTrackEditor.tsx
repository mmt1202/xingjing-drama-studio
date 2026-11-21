import { useEffect, useRef, useState, type AudioHTMLAttributes } from "react";
import {
  AudioLines,
  Captions,
  Check,
  ChevronDown,
  Download,
  FileAudio,
  ListVideo,
  Pencil,
  Plus,
  Save,
  Trash2,
  Upload,
} from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { useAuthenticatedMedia } from "@/hooks/useAuthenticatedMedia";
import { XingjingApiError } from "./api";
import {
  createIdempotencyKey,
  type AudioTrack,
  type AudioTrackKind,
  type SubtitleTrack,
  type TrackVersion,
} from "./contracts";
import type { AudioClient, GenerationAudioWorkspaceProps } from "./GenerationAudioWorkspace";
import { useStudioText } from "./studio-i18n";
import {
  downloadSrt,
  editLine,
  newLine,
  readLines,
  readShotCaptions,
  type CaptionRow,
} from "./subtitle-timeline";

export type StudioTrack = AudioTrack | SubtitleTrack;
export type StudioMedium = "audio" | "subtitle";
export const soundKinds: AudioTrackKind[] = ["dialogue", "voiceover", "bgm", "sfx", "mix"];
interface EditorProps extends Pick<
  GenerationAudioWorkspaceProps,
  "workspaceId" | "projectId" | "episode" | "shotsApi"
> {
  api: AudioClient;
  medium: StudioMedium;
  track?: StudioTrack;
  canManage: boolean;
  canUpload: boolean;
  initialKind: AudioTrackKind;
  onSaved: (track: StudioTrack, version: TrackVersion) => void;
  onDirty: (dirty: boolean) => void;
  onBusy: (busy: boolean) => void;
}

function SoundPlayer(props: AudioHTMLAttributes<HTMLAudioElement>) {
  const { t } = useStudioText();
  // A recording can be auditioned before the creator authors any captions.
  // eslint-disable-next-line jsx-a11y/media-has-caption
  return <audio controls preload="metadata" aria-label={t("audioPlayer")} {...props} />;
}

export function AudioPreview({
  source,
  projectId,
  workspaceId,
}: {
  source: string;
  projectId: string;
  workspaceId?: string;
}) {
  const { t } = useStudioText();
  const media = useAuthenticatedMedia({ source, projectId, workspaceId });
  if (media.error)
    return (
      <div role="alert">
        {t("loadFail")}{" "}
        <button className="as-button" onClick={media.retry}>
          {t("retry")}
        </button>
      </div>
    );
  if (!media.url) return <p role="status">{t("loading")}</p>;
  return <SoundPlayer src={media.url} onError={media.decodeFailed} />;
}

export function StudioTrackEditor(props: EditorProps) {
  const { t } = useStudioText();
  const [revision, setRevision] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [history, setHistory] = useState<{ versions: TrackVersion[]; track: StudioTrack; total: number }>();
  const [olderError, setOlderError] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [recoveryId, setRecoveryId] = useState<string>();
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const { api, projectId, medium } = props;
  const trackId = props.track?.id ?? recoveryId;
  const track = history?.track ?? props.track;
  useEffect(() => {
    if (!trackId) return;
    const abort = new AbortController();
    void Promise.all([
      api.trackVersions(projectId, medium, trackId, abort.signal),
      api.audioTracks(projectId, { media: medium, q: trackId, limit: 1 }, abort.signal),
    ])
      .then(([result, list]) => {
        if (abort.signal.aborted) return;
        const fresh = [...list.data.audioTracks, ...list.data.subtitleTracks].find(
          (row) => row.id === trackId,
        );
        if (!fresh) throw new Error("missing");
        setHistory({
          versions: result.data.versions,
          track: fresh,
          total: result.meta.total ?? result.data.versions.length,
        });
      })
      .catch(() => {
        if (!abort.signal.aborted) setFailed(true);
      });
    return () => abort.abort();
  }, [api, projectId, medium, trackId, refresh]);
  function reload(id?: string) {
    if (id) setRecoveryId(id);
    setDirty(false);
    setFailed(false);
    setHistory(undefined);
    setRevision(0);
    setRefresh((n) => n + 1);
  }
  async function loadOlder() {
    if (!track || !history || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError(false);
    try {
      const offset = history.versions.length;
      const response = await api.trackVersions(projectId, medium, track.id, undefined, offset);
      setHistory((last) => {
        if (!last || last.versions.length !== offset) return last;
        const existing = new Set(last.versions.map((v) => v.id));
        const more = response.data.versions.filter((v) => !existing.has(v.id));
        return {
          ...last,
          versions: [...last.versions, ...more],
          total: more.length ? (response.meta.total ?? last.total) : offset,
        };
      });
    } catch {
      setOlderError(true);
    } finally {
      setLoadingOlder(false);
    }
  }
  if (trackId && !history)
    return (
      <section className="as-card as-empty" role={failed ? "alert" : "status"}>
        {failed ? (
          <>
            {t("loadFail")}{" "}
            <button className="as-button" onClick={() => reload()}>
              {t("retry")}
            </button>
          </>
        ) : (
          t("loading")
        )}
      </section>
    );
  const versions = history?.versions ?? [];
  const version = versions.find((v) => v.revision === revision) ?? versions[0];
  const old = Boolean(version && history && version.revision !== history.track.current_revision);
  return (
    <section className="as-card as-editor">
      {versions.length > 0 && (
        <div className="as-version">
          <span>
            <Check size={15} />
            {t("saved", { number: history!.track.current_revision })}
          </span>
          {versions.length > 1 && (
            <label>
              <ChevronDown size={14} />
              {t("revisions")}
              <select
                aria-label={t("revisions")}
                value={version?.revision}
                disabled={dirty || busy}
                onChange={(e) => setRevision(Number(e.target.value))}
              >
                {versions.map((v) => (
                  <option key={v.id} value={v.revision}>
                    {t("version", { number: v.revision })}
                    {v.revision === history?.track.current_revision ? " · " + t("latest") : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
      {history && history.total > versions.length && (
        <button
          className="as-button as-small"
          disabled={loadingOlder || busy}
          onClick={() => void loadOlder()}
        >
          {t(loadingOlder ? "loading" : "older")}
        </button>
      )}
      {olderError && (
        <p className="as-note" role="alert">
          {t("loadFail")}
        </p>
      )}
      {old && <p className="as-note">{t("oldVersion")}</p>}
      <TrackForm
        key={(track?.id ?? "new") + "-" + (version?.id ?? "")}
        {...props}
        track={history?.track ?? track}
        version={version}
        canManage={props.canManage && !old && track?.state !== "archived"}
        onBusy={(value) => {
          setBusy(value);
          props.onBusy(value);
        }}
        onDirty={(value) => {
          setDirty(value);
          props.onDirty(value);
        }}
        onReload={reload}
      />
    </section>
  );
}

function TrackForm({
  api,
  medium,
  projectId,
  workspaceId,
  episode,
  track,
  version,
  initialKind,
  canUpload,
  canManage,
  shotsApi,
  onSaved,
  onDirty,
  onBusy,
  onReload,
}: EditorProps & { version?: TrackVersion; onReload: (id?: string) => void }) {
  const { t, i18n } = useStudioText();
  const sub = medium === "subtitle";
  const [kind, setKind] = useState<AudioTrackKind>(
    track && "track_kind" in track ? track.track_kind : initialKind,
  );
  const episodeName = episode ? t("episode", { number: episode.number }) + " · " : "";
  const [title, setTitle] = useState(track?.title ?? episodeName + t(sub ? "subtitles" : kind));
  const [lang, setLang] = useState(
    track && "language" in track
      ? track.language
      : ({ en: "en", vi: "vi" }[i18n.resolvedLanguage ?? "zh"] ?? "zh-CN"),
  );
  const storedLines = Array.isArray(version?.cues) ? version.cues : (version?.cues?.cues ?? []);
  const storedLength = Array.isArray(version?.cues) ? version.cues.at(-1)?.endMs : version?.cues?.durationMs;
  const [rows, setRows] = useState<CaptionRow[]>(() => storedLines.map(editLine));
  const [duration, setDuration] = useState(storedLength ?? version?.cue_payload?.durationMs ?? 0);
  const [editing, setEditing] = useState(Boolean(track));
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState("");
  const [fileLength, setFileLength] = useState(0);
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [busy, setWork] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [created, setCreated] = useState<StudioTrack>();
  const [draftId] = useState(() => crypto.randomUUID());
  const submission = useRef<{ signature: string; id: string } | undefined>(undefined);
  const importRequest = useRef<AbortController | undefined>(undefined);
  const saving = useRef(false);
  const alive = useRef(true);
  const authoritative = track ?? created;
  const fileInput = useRef<HTMLInputElement>(null);
  const itemCount = 6;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      importRequest.current?.abort();
    };
  }, []);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );
  function change() {
    setNotice("");
    setDirty(true);
    onDirty(true);
  }
  function work(value: boolean) {
    setWork(value);
    onBusy(value);
  }
  function updateRow(id: string, patch: Partial<CaptionRow>) {
    change();
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }
  function add() {
    change();
    setEditing(true);
    const last = Number(rows.at(-1)?.end ?? 0);
    setRows([...rows, newLine(Number.isFinite(last) ? Math.max(0, Math.round(last * 1000)) : 0)]);
    setOffset(Math.floor(rows.length / itemCount) * itemCount);
  }
  async function fromShots() {
    if (!shotsApi || !workspaceId || !episode || busy) return;
    const abort = new AbortController();
    importRequest.current = abort;
    work(true);
    setNotice("");
    try {
      const result = await readShotCaptions(
        shotsApi,
        { workspaceId, projectId, episodeId: String(episode.number) },
        abort.signal,
      );
      if (abort.signal.aborted) return;
      if (!result.rows.length) {
        setNotice(t("noDialogue"));
        return;
      }
      change();
      setEditing(true);
      setRows(result.rows);
      setDuration(result.durationMs);
      setOffset(0);
    } catch {
      if (!abort.signal.aborted) setNotice(t("sourceChanged"));
    } finally {
      if (!abort.signal.aborted) work(false);
    }
  }
  function chooseFile(next?: File) {
    if (!next) return;
    if (next.size > 64 * 1024 * 1024 || !next.size) {
      setNotice(t(next.size ? "tooBig" : "badFile"));
      return;
    }
    change();
    setEditing(true);
    setFile(next);
    setFileLength(0);
    setPreview(URL.createObjectURL(next));
    if (!authoritative) setTitle(episodeName + next.name.replace(/\.[^.]+$/, ""));
  }
  async function save() {
    if (saving.current || !canManage || conflict) return;
    if (!title.trim()) {
      setNotice(t("emptyName"));
      return;
    }
    const result = readLines(rows);
    if (sub && result.issue) {
      setOffset(Math.floor(result.issue.index / itemCount) * itemCount);
      setNotice(t(result.issue.key, { number: result.issue.index + 1 }));
      return;
    }
    if (sub && !result.lines?.length) {
      setNotice(t("needLine"));
      return;
    }
    if (!sub && (!file || !fileLength || !api.upload)) {
      setNotice(t("needFile"));
      return;
    }
    saving.current = true;
    work(true);
    setNotice("");
    try {
      let objectKey: string | undefined;
      if (file && api.upload) objectKey = (await api.upload(projectId, file)).data.objectKey;
      const id = authoritative?.id ?? draftId;
      let base = authoritative;
      if (!base) {
        const creation = sub
          ? { action: "create_subtitle_track", language: lang, format: "srt" }
          : { action: "create_audio_track", trackKind: kind };
        const response = await api.audioAction(
          projectId,
          { ...creation, title: title.trim(), trackId: id, ...(episode && { episodeId: episode.number }) },
          createIdempotencyKey(projectId, creation.action, id),
        );
        base = response.data.result as StudioTrack;
        if (alive.current) setCreated(base);
      }
      const length = sub ? Math.max(duration, result.lines?.at(-1)?.endMs ?? 0) : fileLength;
      const payload = sub
        ? {
            action: "append_subtitle_version",
            prepareForEditing: canUpload,
            cues: { durationMs: length, cues: result.lines },
          }
        : {
            action: "append_audio_version",
            objectKey,
            cuePayload: {
              durationMs: length,
              ...(kind === "dialogue" || kind === "voiceover"
                ? { clips: [] }
                : { segments: [{ id: "recording", objectKey, startMs: 0, endMs: length }] }),
            },
          };
      const signature = JSON.stringify([base.version, payload]);
      if (submission.current?.signature !== signature)
        submission.current = { signature, id: crypto.randomUUID() };
      const versionId = submission.current.id;
      const response = await api.audioAction(
        projectId,
        { ...payload, trackId: id, versionId },
        createIdempotencyKey(projectId, payload.action, versionId),
        base.version,
      );
      if (alive.current) {
        const saved = response.data.result as TrackVersion;
        setDirty(false);
        onDirty(false);
        onSaved(
          { ...base, current_revision: saved.revision, version: base.version + 1, state: "ready" },
          saved,
        );
      }
    } catch (error) {
      if (!alive.current) return;
      const stale = error instanceof XingjingApiError && error.detail.status === 409;
      setConflict(stale);
      const code = error instanceof XingjingApiError ? error.detail.code : "";
      setNotice(
        t(
          stale
            ? "conflict"
            : code === "AUDIO_UPLOAD_FORMAT_INVALID"
              ? "badFile"
              : code === "AUDIO_UPLOAD_TOO_LARGE"
                ? "tooBig"
                : "saveFail",
        ),
      );
    } finally {
      saving.current = false;
      if (alive.current) work(false);
    }
  }
  const filePicker = (
    <input
      ref={fileInput}
      type="file"
      className="as-file"
      aria-label={t("chooseFile")}
      accept=".mp3,.wav,.m4a,.ogg,.flac"
      disabled={!canManage || !canUpload || busy}
      onChange={(e) => {
        chooseFile(e.target.files?.[0]);
        e.target.value = "";
      }}
    />
  );
  const initial = !editing && !track;
  const Icon = sub ? Captions : AudioLines;
  return (
    <div className="as-form">
      {!sub && filePicker}
      {initial ? (
        <div className="as-start">
          <div className="as-symbol">
            <Icon size={26} />
          </div>
          <h2>{t(sub ? "subStart" : "audioStart")}</h2>
          <p>{t(sub ? (episode ? "subHelp" : "subHelpProject") : "audioHelp")}</p>
          {canManage && (
            <div className="as-actions">
              {sub && episode && shotsApi && (
                <button className="as-button as-primary" disabled={busy} onClick={() => void fromShots()}>
                  <ListVideo />
                  {t(busy ? "importing" : "fromShots")}
                </button>
              )}
              {sub ? (
                <button
                  className={episode ? "as-button" : "as-button as-primary"}
                  disabled={busy}
                  onClick={add}
                >
                  <Pencil />
                  {t("manual")}
                </button>
              ) : (
                canUpload && (
                  <button className="as-button as-primary" onClick={() => fileInput.current?.click()}>
                    <Upload />
                    {t("fromFile")}
                  </button>
                )
              )}
            </div>
          )}
          {!sub && <small>{t(canUpload ? "formats" : "uploadUnavailable")}</small>}
        </div>
      ) : (
        <>
          <div className="as-section-heading">
            <h2>
              <Icon />
              {sub ? t("openEditor") : t("sounds")}
            </h2>
            {dirty && <span className="as-pill">{t("unsaved")}</span>}
          </div>
          <fieldset disabled={busy || !canManage} className="as-fields">
            <div className="as-properties">
              <label>
                {t(sub ? "subtitleName" : "soundName")}
                <input
                  value={title}
                  maxLength={240}
                  readOnly={Boolean(authoritative)}
                  onChange={(e) => {
                    change();
                    setTitle(e.target.value);
                  }}
                />
              </label>
              {sub ? (
                <label>
                  {t("language")}
                  <select
                    value={lang}
                    disabled={Boolean(authoritative)}
                    onChange={(e) => {
                      change();
                      setLang(e.target.value);
                    }}
                  >
                    {!["zh-CN", "en", "vi"].includes(lang) && <option value={lang}>{lang}</option>}
                    <option value="zh-CN">{t("zh")}</option>
                    <option value="en">{t("en")}</option>
                    <option value="vi">{t("vi")}</option>
                  </select>
                </label>
              ) : (
                <label>
                  {t("soundKind")}
                  <select
                    value={kind}
                    disabled={Boolean(authoritative)}
                    onChange={(e) => {
                      change();
                      setKind(e.target.value as AudioTrackKind);
                    }}
                  >
                    {soundKinds.map((k) => (
                      <option key={k} value={k}>
                        {t(k)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {sub && (
              <>
                <div className="as-line-heading">
                  <strong>{t("caption")}</strong>
                  <small>{t("amount", { count: rows.length })}</small>
                </div>
                {rows.slice(offset, offset + itemCount).map((row, i) => {
                  const number = offset + i + 1,
                    label = t("line", { number });
                  return (
                    <div key={row.id} className="as-line">
                      <div className="as-line-label">
                        <strong>{label}</strong>
                        {canManage && (
                          <button
                            type="button"
                            aria-label={t("remove", { number })}
                            title={t("remove", { number })}
                            onClick={() => {
                              change();
                              setRows(rows.filter((item) => item.id !== row.id));
                              setOffset(
                                Math.min(
                                  offset,
                                  Math.max(0, Math.floor((rows.length - 2) / itemCount) * itemCount),
                                ),
                              );
                            }}
                          >
                            <Trash2 size={15} />
                          </button>
                        )}
                      </div>
                      <label>
                        {t("start")}
                        <input
                          aria-label={label + " " + t("start")}
                          type="number"
                          min="0"
                          step="0.001"
                          value={row.start}
                          onChange={(e) => updateRow(row.id, { start: e.target.value })}
                        />
                      </label>
                      <label>
                        {t("end")}
                        <input
                          aria-label={label + " " + t("end")}
                          type="number"
                          min="0"
                          step="0.001"
                          value={row.end}
                          onChange={(e) => updateRow(row.id, { end: e.target.value })}
                        />
                      </label>
                      <textarea
                        aria-label={label + " " + t("caption")}
                        rows={2}
                        value={row.text}
                        onChange={(e) => updateRow(row.id, { text: e.target.value })}
                      />
                    </div>
                  );
                })}
                {canManage && (
                  <button type="button" className="as-button as-add" onClick={add}>
                    <Plus />
                    {t("addLine")}
                  </button>
                )}
                {canManage && !rows.length && episode && shotsApi && (
                  <button type="button" className="as-button as-add" onClick={() => void fromShots()}>
                    <ListVideo />
                    {t("fromShots")}
                  </button>
                )}
              </>
            )}
          </fieldset>
          {sub && rows.length > itemCount && (
            <Pagination offset={offset} pageSize={itemCount} total={rows.length} onOffsetChange={setOffset} />
          )}
          {!sub && (
            <div className="as-audio-box">
              {preview ? (
                <>
                  <strong>
                    <FileAudio size={16} />
                    {file?.name}
                  </strong>
                  <SoundPlayer
                    src={preview}
                    onLoadedMetadata={(e) => {
                      const seconds = e.currentTarget.duration;
                      if (Number.isFinite(seconds) && seconds > 0) setFileLength(Math.round(seconds * 1000));
                      else setNotice(t("badFile"));
                    }}
                    onError={() => {
                      setFileLength(0);
                      setNotice(t("badFile"));
                    }}
                  />
                  <small>{fileLength ? t("seconds", { value: fileLength / 1000 }) : t("readingAudio")}</small>
                </>
              ) : version?.object_key ? (
                <AudioPreview
                  source={api.audioObjectUrl(projectId, version.object_key)}
                  projectId={projectId}
                  workspaceId={workspaceId}
                />
              ) : (
                <p>{t("noRecording")}</p>
              )}
              {canManage &&
                (canUpload ? (
                  <button className="as-button" disabled={busy} onClick={() => fileInput.current?.click()}>
                    <Upload />
                    {t(preview || version?.object_key ? "replaceFile" : "chooseFile")}
                  </button>
                ) : (
                  <p>{t("uploadUnavailable")}</p>
                ))}
            </div>
          )}
          <div className="as-footer">
            {canManage && (
              <button
                className="as-button as-primary"
                disabled={
                  (!dirty && !(sub && version && !version.object_key && canUpload)) ||
                  busy ||
                  conflict ||
                  (!sub && !fileLength)
                }
                onClick={() => void save()}
              >
                <Save />
                {t(busy ? "saving" : sub ? "saveSubs" : "saveSound")}
              </button>
            )}
            {sub && storedLines.length > 0 && !dirty && (
              <button className="as-button" onClick={() => downloadSrt(storedLines, title)}>
                <Download />
                {t("download")}
              </button>
            )}
          </div>
        </>
      )}
      {notice && (
        <div className="as-notice" role="alert">
          {notice}
          {conflict && (
            <button
              className="as-button"
              onClick={() => {
                if (window.confirm(t("discardAsk"))) {
                  onDirty(false);
                  onReload(authoritative?.id ?? draftId);
                }
              }}
            >
              {t("recheckConflict")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
