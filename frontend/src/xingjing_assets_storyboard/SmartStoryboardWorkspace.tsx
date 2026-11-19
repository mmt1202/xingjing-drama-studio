import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Boxes,
  Check,
  ChevronDown,
  Circle,
  Clapperboard,
  Film,
  ListFilter,
  RefreshCw,
  Search,
  Video,
} from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { AssetsStoryboardApiError } from "./api";
import type {
  AssetsStoryboardPort,
  SmartEpisodeInputs,
  SmartStoryboardInputs,
  WorkspaceProjectScope,
} from "./contracts";
import { useDraftProtection } from "./useDraftProtection";
import { useSmartShotsTranslation } from "./smart-i18n";
import { SmartShotDraft } from "./SmartShotDraft";
import "./smart-storyboard.css";

export type SmartNavigation = (path: string, episode?: number) => void;
type Feedback = { key: string; count?: number; warning?: boolean };
type Props = {
  scope: WorkspaceProjectScope;
  api: AssetsStoryboardPort;
  onOpen?: SmartNavigation;
  scriptVersionId?: string;
  assetSnapshotId?: string;
  onChange?: () => void;
};

function errorKey(error: unknown) {
  if (!(error instanceof AssetsStoryboardApiError) || error.status === null) return "unknown";
  if (error.code === "SMART_STORYBOARD_MODEL_FAILED") return "modelFailed";
  if (error.code === "SMART_STORYBOARD_REQUEST_IN_PROGRESS") return "inProgress";
  if (error.code === "DIRECTOR_CONFIRMATION_REQUIRED") return "needDirector";
  if (error.code === "EPISODE_STORYBOARD_INPUTS_CHANGED") return "changed";
  if (error.status === 403) return "readOnly";
  if (error.code.startsWith("FROZEN_SCRIPT") || error.code.startsWith("ASSET_SNAPSHOT")) return "changed";
  return error.status === 409 ? "conflict" : "actionFailed";
}

function EpisodePicker({
  episodes,
  selected,
  choose,
  openEpisodes,
}: {
  episodes: SmartStoryboardInputs["episodes"];
  selected?: number;
  choose: (n: number) => void;
  openEpisodes?: () => void;
}) {
  const { t } = useSmartShotsTranslation();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const query = search.trim().toLocaleLowerCase();
  const matches = episodes.filter(
    (e) =>
      !query ||
      e.title.toLocaleLowerCase().includes(query) ||
      String(e.number) === query ||
      t("episode", { number: e.number })
        .replace(/\s/g, "")
        .toLocaleLowerCase()
        .includes(query.replace(/\s/g, "")),
  );
  const pageSize = 6;
  return (
    <section className="ss-panel ss-picker" aria-label={t("picker")}>
      <div className="ss-heading">
        <div>
          <h2>{t("picker")}</h2>
          <p>{t("selectHint")}</p>
        </div>
        <label className="ss-search">
          <Search size={16} />
          <input
            aria-label={t("search")}
            placeholder={t("search")}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>
      {matches.length ? (
        <>
          <div className="ss-episodes">
            {matches.slice(offset, offset + pageSize).map((e) => (
              <button
                className="ss-episode"
                key={e.number}
                type="button"
                aria-current={e.number === selected ? "true" : undefined}
                onClick={() => choose(e.number)}
              >
                <span className="ss-episode-no">{t("episode", { number: e.number })}</span>
                <strong>{e.title}</strong>
                <span className={e.bound ? "ss-good" : "ss-muted"}>{t(e.bound ? "bound" : "unbound")}</span>
                <ArrowRight size={15} />
              </button>
            ))}
          </div>
          {matches.length > pageSize && (
            <Pagination
              label={t("episodePage")}
              unit="episodes"
              offset={offset}
              pageSize={pageSize}
              total={matches.length}
              onOffsetChange={setOffset}
            />
          )}
        </>
      ) : (
        <p className="ss-empty">
          {t(episodes.length ? "noMatch" : "noEpisodes")}{" "}
          {!episodes.length && openEpisodes && (
            <button className="ss-link" onClick={openEpisodes}>
              {t("openEpisodes")}
            </button>
          )}
        </p>
      )}
    </section>
  );
}

function Preparation({ input, open }: { input: SmartEpisodeInputs; open?: SmartNavigation }) {
  const { t } = useSmartShotsTranslation();
  const scriptPage = input.scriptId
    ? `versions?${new URLSearchParams({ scriptId: input.scriptId })}`
    : "episodes";
  const directorPage = input.scriptId
    ? `ai-director?${new URLSearchParams({ scriptId: input.scriptId })}`
    : "ai-director";
  const script = {
    ready: "scriptReady",
    missing: "scriptMissing",
    bind: "scriptBind",
    stale: "scriptStale",
    invalid: "scriptInvalid",
    denied: "denied",
  }[input.scriptState];
  const director = {
    ready: "directorReady",
    missing: "directorMissing",
    unconfirmed: "directorUnconfirmed",
    waiting: "wait",
    denied: "denied",
  }[input.directorState];
  const assets = {
    ready: "assetReady",
    missing: "assetMissing",
    bind: "assetBind",
    stale: "assetStale",
    waiting: "wait",
    denied: "denied",
  }[input.assetState];
  const scriptLink =
    input.scriptState === "bind" || input.scriptState === "stale"
      ? "episodes"
      : input.scriptState === "invalid" && input.scriptId
        ? "script-lab?" + new URLSearchParams({ scriptId: input.scriptId }).toString()
        : scriptPage;
  const rows = [
    {
      label: "script",
      Icon: BookOpen,
      status: input.scriptState,
      text: t(script, { title: input.scriptTitle, version: input.version, count: input.sceneCount }),
      path: scriptLink,
      action:
        scriptLink === "episodes"
          ? input.scriptId
            ? "bindScript"
            : "openEpisodes"
          : input.scriptState === "invalid"
            ? "parse"
            : "freeze",
    },
    {
      label: "director",
      Icon: Clapperboard,
      status: input.directorState,
      text: t(director, { number: input.candidateNumber }),
      path: directorPage,
      action: "openDirector",
    },
    {
      label: "assets",
      Icon: Boxes,
      status: input.assetState,
      text: t(assets, { count: input.assetCount }),
      path: input.assetState === "missing" ? "assets-extract" : "episodes",
      action: input.assetState === "missing" ? "extract" : "bindAssets",
    },
  ];
  return (
    <section className="ss-panel" aria-label={t("inputs")}>
      <div className="ss-heading">
        <h2>{t("inputs")}</h2>
      </div>
      <div className="ss-inputs">
        {rows.map(({ label, Icon, status, text, path, action }) => (
          <div className="ss-input" key={label}>
            <div className="ss-input-title">
              <Icon size={18} />
              <h3>{t(label)}</h3>
              {status === "ready" ? (
                <Check aria-label={t("ready")} size={16} className="ss-good" />
              ) : (
                <Circle size={12} aria-hidden />
              )}
            </div>
            <p>{text}</p>
            {open && !["ready", "denied", "waiting"].includes(status) && (
              <button
                className="ss-link"
                onClick={() =>
                  open(path + (path.includes("?") ? "&" : "?") + "returnTo=shots-smart", input.number)
                }
              >
                {t(action)}
                <ArrowRight size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

export function SmartStoryboardWorkspace({
  scope,
  api,
  onOpen,
  onChange,
  scriptVersionId,
  assetSnapshotId,
}: Props) {
  const { t } = useSmartShotsTranslation();
  const [localEpisode, setLocalEpisode] = useState<number>();
  const episodeId = scope.episodeId ?? localEpisode?.toString();
  const [revision, setRevision] = useState(0);
  const refresh = () => setRevision((v) => v + 1);
  const scopeKey = JSON.stringify([scope.workspaceId, scope.projectId, episodeId]);
  const key = JSON.stringify([scopeKey, revision]);
  const [read, setRead] = useState<{
    key: string;
    scopeKey: string;
    value?: SmartStoryboardInputs;
    error?: string;
  }>();
  const [picker, setPicker] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmAnother, setConfirmAnother] = useState<string>();
  const [createdBoard, setCreatedBoard] = useState<string>();
  const [feedback, setFeedback] = useState<Feedback>();
  const locked = useRef(false);
  const alive = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useDraftProtection(busy);
  useEffect(() => {
    const ctl = new AbortController();
    const scoped = { workspaceId: scope.workspaceId, projectId: scope.projectId, episodeId };
    void Promise.resolve()
      .then(async () => {
        if (!api.getSmartInputs) throw new Error("SMART_INPUTS_UNAVAILABLE");
        const value = await api.getSmartInputs(scoped, ctl.signal);
        if (!ctl.signal.aborted) setRead({ key, scopeKey, value });
      })
      .catch((e: unknown) => {
        if (!ctl.signal.aborted)
          setRead({
            key,
            scopeKey,
            error: e instanceof AssetsStoryboardApiError && e.status === 403 ? "loadDenied" : "loadError",
          });
      });
    return () => ctl.abort();
  }, [api, key, scopeKey, scope.workspaceId, scope.projectId, episodeId]);
  const current = read?.scopeKey === scopeKey ? read : undefined,
    input = current?.value?.selected;
  const refreshing = current?.key !== key;
  const stale = Boolean(
    input &&
      ((scriptVersionId !== undefined && scriptVersionId !== (input.scriptVersionId ?? "")) ||
        (assetSnapshotId !== undefined && assetSnapshotId !== (input.assetSnapshotId ?? ""))),
  );
  function choose(n: number) {
    if (locked.current) return;
    setPicker(false);
    setFeedback(undefined);
    if (onOpen) onOpen("shots-smart", n);
    else setLocalEpisode(n);
  }
  async function generate() {
    if (!input?.command || refreshing || stale || !api.getSmartInputs || locked.current) return;
    locked.current = true;
    setConfirmAnother(undefined);
    setBusy(true);
    setFeedback(undefined);
    try {
      const scoped = { ...scope, episodeId: String(input.number) };
      const fresh = await api.getSmartInputs(scoped);
      if (!alive.current) return;
      const selected = fresh.selected;
      if (
        !selected?.command ||
        selected.command.key !== input.command.key ||
        selected.confirmationId !== input.confirmationId
      ) {
        setRead({ key, scopeKey, value: fresh });
        setFeedback({
          key: selected?.boards.length ? "existing" : "changed",
          warning: !selected?.boards.length,
        });
        return;
      }
      const result = await api.shotAction(scoped, {
        action: "generateShots",
        payload: selected.command.payload,
        idempotencyKey: selected.command.key,
      });
      if (!alive.current) return;
      setCreatedBoard(result.shots?.[0]?.storyboardId);
      setFeedback(
        result.shots?.length
          ? { key: "created", count: result.shots.length }
          : { key: "unknown", warning: true },
      );
      refresh();
      onChange?.();
    } catch (e) {
      if (alive.current) {
        setFeedback({ key: errorKey(e), warning: true });
        refresh();
      }
    } finally {
      locked.current = false;
      if (alive.current) setBusy(false);
    }
  }
  return (
    <main className="ss-workspace" aria-label={t("title")} aria-busy={busy}>
      <header className="ss-top">
        <div className="ss-title">
          <span className="ss-mark">
            <Clapperboard size={24} />
          </span>
          <div>
            <h1>{t("title")}</h1>
            <p>{t("intro")}</p>
          </div>
        </div>
        <button type="button" className="ss-btn" disabled={busy} onClick={refresh}>
          <RefreshCw size={15} />
          {t("reload")}
        </button>
      </header>
      {feedback && (
        <p role="status" className={feedback.warning ? "ss-alert" : "ss-success"}>
          {t(feedback.key, { count: feedback.count })}
        </p>
      )}
      {!current ? (
        <p role="status" className="ss-empty">
          {t("loading")}
        </p>
      ) : current.error ? (
        <p role="alert" className="ss-alert">
          {t(current.error)}{" "}
          <button className="ss-link" onClick={refresh}>
            {t("retry")}
          </button>
        </p>
      ) : (
        <>
          {input && (
            <div className="ss-current">
              <div>
                <span className="ss-episode-no">{t("episode", { number: input.number })}</span>
                <strong>{input.title}</strong>
              </div>
              <button
                className="ss-btn"
                disabled={busy}
                aria-expanded={picker}
                onClick={() => setPicker(!picker)}
              >
                <ListFilter size={15} />
                {t(picker ? "close" : "change")}
                <ChevronDown size={14} />
              </button>
            </div>
          )}
          {current.value && (!input || picker) && (
            <EpisodePicker
              episodes={current.value.episodes}
              selected={input?.number}
              choose={choose}
              openEpisodes={onOpen ? () => onOpen("episodes") : undefined}
            />
          )}
          {input &&
            (stale ? (
              <div role="status" className="ss-alert">
                {t("stale")}{" "}
                {onOpen && (
                  <button
                    className="ss-link"
                    onClick={() =>
                      onOpen(
                        "shots-smart?" +
                          new URLSearchParams({
                            scriptVersionId: input.scriptVersionId ?? "",
                            assetSnapshotId: input.assetSnapshotId ?? "",
                          }).toString(),
                        input.number,
                      )
                    }
                  >
                    {t("latest")}
                  </button>
                )}
              </div>
            ) : (
              <>
                <Preparation input={input} open={busy ? undefined : onOpen} />
                {input.boards.length ? (
                  <>
                    <SmartShotDraft
                      key={createdBoard ?? input.number}
                      initialBoardId={createdBoard}
                      scope={{ ...scope, episodeId: String(input.number) }}
                      api={api}
                      input={input}
                      onOpen={onOpen}
                      refreshing={refreshing || busy}
                      onChange={() => {
                        refresh();
                        onChange?.();
                      }}
                    />
                    {input.command && (
                      <div className="ss-tools">
                        {confirmAnother === input.command.key ? (
                          <>
                            <p className="ss-note">{t("anotherHint")}</p>
                            <button
                              type="button"
                              className="ss-primary"
                              disabled={busy || refreshing}
                              onClick={() => void generate()}
                            >
                              {t("confirmAnother")}
                            </button>
                            <button
                              type="button"
                              className="ss-btn"
                              onClick={() => setConfirmAnother(undefined)}
                            >
                              {t("cancel")}
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="ss-btn"
                            disabled={busy || refreshing}
                            onClick={() => setConfirmAnother(input.command?.key)}
                          >
                            <Film size={15} />
                            {t(busy ? "generating" : "another")}
                          </button>
                        )}
                      </div>
                    )}
                  </>
                ) : (
                  <section className="ss-panel ss-generate">
                    <div className="ss-generate-copy">
                      <Film size={23} />
                      <div>
                        <h2>{t("board")}</h2>
                        <p>
                          {t(
                            input.command ? "submittingHint" : !input.canManage ? "readOnly" : "prerequisite",
                          )}
                        </p>
                      </div>
                    </div>
                    <button
                      className="ss-primary"
                      disabled={busy || refreshing || !input.command}
                      onClick={() => void generate()}
                    >
                      <Video size={17} />
                      {t(busy ? "generating" : "generate")}
                    </button>
                  </section>
                )}
              </>
            ))}
        </>
      )}
    </main>
  );
}
