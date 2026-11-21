import { useEffect, useState } from "react";
import { ArrowRight, AudioLines, Captions, ClipboardList, Plus, Search } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { useOptionalProjectFlow } from "@/xingjing_creator_projects/ProjectFlowShell";
import { XingjingApiError } from "./api";
import type { AudioTracksData } from "./contracts";
import type { AudioClient, GenerationAudioWorkspaceProps } from "./GenerationAudioWorkspace";
import { StudioHistory, TrackProduction } from "./StudioProduction";
import { StudioTrackEditor, type StudioMedium, type StudioTrack } from "./StudioTrackEditor";
import { usePaidMediaAction } from "./usePaidMediaAction";
import { useStudioText } from "./studio-i18n";
import "./audio-studio.css";

type Tab = StudioMedium | "history";
type View = { tab: Tab; whole: boolean; search: string; offset: number; selected: string | null };
const pageSize = 8;
const tabs = [
  { key: "subtitle", label: "subtitles", Icon: Captions },
  { key: "audio", label: "sounds", Icon: AudioLines },
  { key: "history", label: "tasks", Icon: ClipboardList },
] as const;

function readView(key: string, fallback: View): View {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? "null") as Partial<View> | null;
    if (!saved || !tabs.some((tab) => tab.key === saved.tab)) return fallback;
    return {
      tab: saved.tab ?? fallback.tab,
      whole: saved.whole === true,
      search: typeof saved.search === "string" ? saved.search : "",
      offset: Number.isSafeInteger(saved.offset) && Number(saved.offset) >= 0 ? Number(saved.offset) : 0,
      selected: typeof saved.selected === "string" ? saved.selected : null,
    };
  } catch {
    return fallback;
  }
}

export function AudioStudio({
  api,
  epoch,
  pageId,
  projectId,
  workspaceId,
  projectName,
  episode,
  shotsApi,
  onEditFilm,
}: GenerationAudioWorkspaceProps & { api: AudioClient; epoch: string }) {
  const { t } = useStudioText();
  const register = useOptionalProjectFlow()?.registerGuard;
  const viewKey = JSON.stringify(["sound-studio", epoch, workspaceId, projectId, episode?.number, pageId]);
  const [view, setView] = useState(() =>
    readView(viewKey, {
      tab: pageId === "CR-008" ? "audio" : "subtitle",
      whole: !episode,
      search: "",
      offset: 0,
      selected: null,
    }),
  );
  const [library, setLibrary] = useState<{ key: string; data: AudioTracksData; total: number }>();
  const [failure, setFailure] = useState<{ key: string; forbidden: boolean }>();
  const [refresh, setRefresh] = useState(0);
  const [editorKey, setEditorKey] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const medium: StudioMedium = view.tab === "audio" ? "audio" : "subtitle";
  const episodeId = !view.whole ? episode?.number : undefined;
  const requestKey = JSON.stringify([medium, episodeId, view.search.trim(), view.offset]);

  useEffect(() => {
    try {
      sessionStorage.setItem(viewKey, JSON.stringify(view));
    } catch {
      /* The workspace works without saved navigation. */
    }
  }, [view, viewKey]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => {
        void api
          .audioTracks(
            projectId,
            { media: medium, episodeId, q: view.search.trim(), offset: view.offset, limit: pageSize },
            controller.signal,
          )
          .then((response) => {
            if (controller.signal.aborted) return;
            const total =
              response.meta.total ??
              (medium === "audio" ? response.data.audioTracks : response.data.subtitleTracks).length;
            if (view.offset > 0 && view.offset >= total) {
              setView((last) => ({
                ...last,
                offset: Math.max(0, Math.floor((total - 1) / pageSize) * pageSize),
              }));
              return;
            }
            setFailure(undefined);
            setLibrary({ key: requestKey, data: response.data, total });
          })
          .catch((error) => {
            if (!controller.signal.aborted)
              setFailure({
                key: requestKey,
                forbidden: error instanceof XingjingApiError && [401, 403].includes(error.detail.status),
              });
          });
      },
      view.search ? 180 : 0,
    );
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [api, projectId, medium, episodeId, view.search, view.offset, requestKey, refresh]);
  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);
  useEffect(
    () => register?.(() => !busy && (!dirty || window.confirm(t("discardAsk")))),
    [register, busy, dirty, t],
  );
  function navigate(patch: Partial<View>) {
    if (busy || (dirty && !window.confirm(t("discardAsk")))) return;
    setDirty(false);
    setNotice("");
    setEditorKey((n) => n + 1);
    setView((last) => ({ ...last, ...patch }));
  }
  const paid = usePaidMediaAction(
    api,
    projectId,
    workspaceId,
    epoch,
    () => {
      setNotice(t("submitted"));
      setRefresh((n) => n + 1);
    },
    (cause) =>
      setNotice(
        t(
          cause instanceof XingjingApiError && cause.detail.code.includes("INSUFFICIENT")
            ? "insufficient"
            : "failedAction",
        ),
      ),
  );
  const active = library?.key === requestKey ? library : undefined;
  const problem = failure?.key === requestKey ? failure : undefined;
  const reload = () => {
    setFailure(undefined);
    setRefresh((n) => n + 1);
  };
  if ((!library && !problem) || problem?.forbidden)
    return (
      <section className="as-studio as-gate" aria-label={t("region")} aria-busy={!problem}>
        {problem ? <h2>{t("forbidden")}</h2> : <p role="status">{t("loading")}</p>}
      </section>
    );
  const canManage = active?.data.canManage === true;
  const rows = (medium === "audio" ? active?.data.audioTracks : active?.data.subtitleTracks) ?? [];
  const selected = view.selected === null ? rows[0] : rows.find((row) => row.id === view.selected);
  const Icon = medium === "audio" ? AudioLines : Captions;
  const costs = {
    api,
    projectId,
    workspaceId,
    locked: paid.busy || Boolean(paid.original) || dirty || busy,
    submit: paid.submit,
  };
  function saved(track: StudioTrack) {
    setDirty(false);
    setBusy(false);
    setNotice(t("next"));
    setEditorKey((n) => n + 1);
    setView((last) => ({ ...last, offset: 0, search: "", selected: track.id }));
    setRefresh((n) => n + 1);
  }
  return (
    <section className="as-studio" aria-label={t("region")}>
      <header className="as-header">
        <div>
          <p className="as-context">
            {[
              projectName,
              episode ? t("episode", { number: episode.number }) : t("allProject"),
              episode?.title,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <h1>{t("title")}</h1>
          <p>{t("intro")}</p>
        </div>
        {onEditFilm && (
          <button className="as-button as-next" disabled={busy} onClick={onEditFilm}>
            {t("editing")}
            <ArrowRight />
          </button>
        )}
      </header>
      <nav className="as-tabs" aria-label={t("region")}>
        {tabs.map(({ key, label, Icon: TabIcon }) => (
          <button
            key={key}
            aria-pressed={view.tab === key}
            onClick={() => {
              if (key !== view.tab) navigate({ tab: key, search: "", offset: 0, selected: null });
            }}
          >
            <TabIcon />
            {t(label)}
          </button>
        ))}
      </nav>
      {problem ? (
        <div role="alert" className="as-notice">
          {t("loadFail")}{" "}
          <button className="as-button" onClick={reload}>
            {t("retry")}
          </button>
        </div>
      ) : (
        <>
          {active && !canManage && <p className="as-note">{t("readOnly")}</p>}
          {notice && (
            <div className="as-notice" role="status">
              {notice}
            </div>
          )}
          {paid.original && (
            <div className="as-notice" role="status">
              {t("recovery")}{" "}
              <button
                className="as-button"
                disabled={paid.busy || !canManage}
                onClick={() => {
                  if (paid.original) void paid.execute(paid.original);
                }}
              >
                {t("check")}
              </button>
            </div>
          )}
          {view.tab === "history" ? (
            active ? (
              <StudioHistory {...costs} revision={refresh} canManage={canManage} />
            ) : (
              <p role="status">{t("loading")}</p>
            )
          ) : (
            <div className="as-layout">
              <aside className="as-card as-library">
                <div className="as-section-heading">
                  <h2>{t(medium === "audio" ? "soundLibrary" : "library")}</h2>
                  {canManage && (
                    <button className="as-button as-small" onClick={() => navigate({ selected: "" })}>
                      <Plus />
                      {t(medium === "audio" ? "newAudio" : "newSub")}
                    </button>
                  )}
                </div>
                {episode && (
                  <label className="as-scope">
                    {t("scope")}
                    <select
                      value={view.whole ? "all" : "episode"}
                      onChange={(e) =>
                        navigate({ whole: e.target.value === "all", offset: 0, selected: null })
                      }
                    >
                      <option value="episode">{t("episode", { number: episode.number })}</option>
                      <option value="all">{t("allProject")}</option>
                    </select>
                  </label>
                )}
                <label className="as-search">
                  <Search size={15} />
                  <input
                    type="search"
                    aria-label={t("search")}
                    placeholder={t("search")}
                    value={view.search}
                    onChange={(e) => navigate({ search: e.target.value, offset: 0, selected: null })}
                  />
                </label>
                {!active ? (
                  <p className="as-list-empty" role="status">
                    {t("loading")}
                  </p>
                ) : !rows.length ? (
                  <p className="as-list-empty">{t(view.search ? "noMatches" : "emptyLibrary")}</p>
                ) : (
                  <div className="as-tracks">
                    {rows.map((row) => (
                      <button
                        key={row.id}
                        aria-pressed={row.id === selected?.id}
                        onClick={() => {
                          if (row.id !== selected?.id) navigate({ selected: row.id });
                        }}
                      >
                        <Icon size={18} />
                        <span>
                          <strong>{row.title}</strong>
                          <small>
                            {[
                              row.episode_number
                                ? t("episode", { number: row.episode_number })
                                : t("sharedTrack"),
                              "track_kind" in row ? t(row.track_kind) : undefined,
                              row.state === "archived"
                                ? t("archived")
                                : row.current_revision
                                  ? t("version", { number: row.current_revision })
                                  : t("draft"),
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </small>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                {active && active.total > pageSize && (
                  <Pagination
                    offset={view.offset}
                    pageSize={pageSize}
                    total={active.total}
                    disabled={busy}
                    onOffsetChange={(offset) => navigate({ offset, selected: null })}
                  />
                )}
              </aside>
              <div className="as-content">
                {active ? (
                  <>
                    <StudioTrackEditor
                      key={[medium, selected?.id ?? "", editorKey].join("-")}
                      api={api}
                      workspaceId={workspaceId}
                      projectId={projectId}
                      episode={episode}
                      shotsApi={shotsApi}
                      medium={medium}
                      track={selected}
                      initialKind={pageId === "CR-008" ? "bgm" : "voiceover"}
                      canManage={canManage}
                      canUpload={Boolean(active.data.services?.canUpload && api.upload)}
                      onSaved={saved}
                      onDirty={setDirty}
                      onBusy={setBusy}
                    />
                    {canManage &&
                      selected &&
                      selected.current_revision > 0 &&
                      selected.state !== "archived" && (
                        <TrackProduction
                          key={selected.id}
                          {...costs}
                          track={selected}
                          episode={episode?.number}
                          services={active.data.services}
                        />
                      )}
                  </>
                ) : (
                  <p className="as-card as-empty" role="status">
                    {t("loading")}
                  </p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
