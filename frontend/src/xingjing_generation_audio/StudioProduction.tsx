import { useEffect, useRef, useState } from "react";
import { ChevronDown, ClipboardList, RefreshCw } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { BudgetScopePicker, type BudgetScope } from "@/xingjing_budget/BudgetScopePicker";
import { useStudioText } from "./studio-i18n";
import { AudioPreview, type StudioTrack } from "./StudioTrackEditor";
import type { AudioClient } from "./GenerationAudioWorkspace";
import type { AudioTracksData, MediaTask, MediaTaskKind } from "./contracts";
import { createIdempotencyKey } from "./contracts";
import type { PaidAudioPayload } from "./paid-request-recovery";

const taskLabels = {
  audio: "taskAudio",
  subtitle: "taskSubtitle",
  mix: "taskMix",
  lip_sync: "taskLip",
} as const;
const statusKeys = new Set([
  "pending",
  "queued",
  "running",
  "cancelling",
  "retrying",
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
]);
function amount(minor: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, { style: "currency", currency }).format(minor / 100);
}
interface ProductionProps {
  api: AudioClient;
  projectId: string;
  workspaceId?: string;
  locked: boolean;
  submit: (payload: PaidAudioPayload, version?: number) => void;
}

export function TrackProduction({
  track,
  episode,
  services,
  ...props
}: ProductionProps & {
  track: StudioTrack;
  episode?: number;
  services: AudioTracksData["services"];
}) {
  const { t, i18n } = useStudioText();
  const [open, setOpen] = useState(false);
  const kind: MediaTaskKind =
    "track_kind" in track ? (["bgm", "sfx", "mix"].includes(track.track_kind) ? "mix" : "audio") : "subtitle";
  const estimate = services?.prices[kind];
  return (
    <details className="as-production" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        {t("auto")}
        <ChevronDown size={16} />
      </summary>
      {open &&
        (estimate !== undefined && services?.currency ? (
          <>
            <p>
              {t("amountEstimate", {
                value: amount(estimate, services.currency, i18n.resolvedLanguage ?? "zh"),
              })}
            </p>
            <Allocation
              {...props}
              track={track}
              initialEpisode={track.episode_number ?? episode}
              kind={kind}
            />
          </>
        ) : (
          <p>{t("unavailable")}</p>
        ))}
    </details>
  );
}

function Allocation({
  api,
  workspaceId,
  projectId,
  track,
  initialEpisode,
  kind,
  locked,
  submit,
}: ProductionProps & {
  track: StudioTrack;
  initialEpisode?: number;
  kind: MediaTaskKind;
}) {
  const { t } = useStudioText();
  const [scope, setScope] = useState<BudgetScope | undefined>(
    initialEpisode ? { kind: "episode", episodeId: initialEpisode } : undefined,
  );
  const [ready, setReady] = useState(false);
  const [binding, setBinding] = useState<BudgetScope | null>();
  const [failure, setFailure] = useState(false);
  const [retry, setRetry] = useState(0);
  const resource = "track_kind" in track ? "audio_track" : "subtitle_track";
  useEffect(() => {
    const controller = new AbortController();
    if (api.budgetBinding)
      void api
        .budgetBinding(projectId, resource, track.id, controller.signal)
        .then((response) => {
          if (!controller.signal.aborted) setBinding(response.data.budgetScope);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailure(true);
        });
    return () => controller.abort();
  }, [api, projectId, resource, track.id, retry]);
  if (!api.budgetBinding) return <p>{t("unavailable")}</p>;
  if (failure)
    return (
      <button
        className="as-button"
        onClick={() => {
          setFailure(false);
          setRetry((n) => n + 1);
        }}
      >
        {t("reload")}
      </button>
    );
  if (binding === undefined) return <p role="status">{t("loading")}</p>;
  return (
    <>
      {binding ? (
        <p>
          {t("confirmedScope", {
            scope: binding.kind === "episode" ? t("episode", { number: binding.episodeId }) : t("shared"),
          })}
        </p>
      ) : (
        <BudgetScopePicker
          projectId={projectId}
          workspaceId={workspaceId}
          value={scope}
          onChange={setScope}
          onReady={setReady}
          disabled={locked}
        />
      )}
      <button
        className="as-button"
        disabled={locked || (!binding && !ready)}
        onClick={() =>
          submit({
            action: "create_media_task",
            taskKind: kind,
            taskId: crypto.randomUUID(),
            resourceType: resource,
            resourceId: track.id,
            maxAttempts: 2,
            ...(binding || !scope ? {} : { budgetScope: scope }),
          })
        }
      >
        {t("generate")}
      </button>
    </>
  );
}

export function StudioHistory({
  api,
  projectId,
  workspaceId,
  locked,
  submit,
  revision,
  canManage,
}: ProductionProps & { revision: number; canManage: boolean }) {
  const { t, i18n } = useStudioText();
  const [list, setList] = useState<{ key: string; rows: MediaTask[]; total: number }>();
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [failed, setFailed] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const writing = useRef(false);
  const key = JSON.stringify([search, offset, revision, refresh]);
  useEffect(() => {
    const abort = new AbortController();
    const timer = window.setTimeout(
      () => {
        void api
          .audioTasks(projectId, { offset, limit: 10, q: search.trim() }, abort.signal)
          .then((res) => {
            if (abort.signal.aborted) return;
            const total = res.meta.total ?? res.data.tasks.length;
            if (offset > 0 && offset >= total) {
              setOffset(Math.max(0, Math.floor((total - 1) / 10) * 10));
              return;
            }
            setList({ key, rows: res.data.tasks, total });
          })
          .catch(() => {
            if (!abort.signal.aborted) setFailed(key);
          });
      },
      search ? 180 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      abort.abort();
    };
  }, [api, projectId, key, offset, search]);
  const current = list?.key === key ? list : undefined;
  const active = current?.rows.some((task) =>
    ["pending", "queued", "running", "retrying", "cancelling"].includes(task.status),
  );
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => setRefresh((n) => n + 1), 6000);
    return () => window.clearTimeout(timer);
  }, [active, key]);
  async function action(task: MediaTask, cancel: boolean) {
    if (writing.current || locked) return;
    const name = cancel ? "cancel_media_task" : "record_media_fallback";
    writing.current = true;
    setBusy(true);
    setError("");
    try {
      await api.audioAction(
        projectId,
        { action: name, taskId: task.id, reason: t(cancel ? "cancel" : "keep") },
        createIdempotencyKey(projectId, name, task.id + "-" + task.version),
        task.version,
      );
      setRefresh((n) => n + 1);
    } catch {
      setError(t("failedAction"));
    } finally {
      writing.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="as-card as-history">
      <div className="as-section-heading">
        <h2>
          <ClipboardList />
          {t("historyScope")}
        </h2>
        <button className="as-button" onClick={() => setRefresh((n) => n + 1)}>
          <RefreshCw />
          {t("reload")}
        </button>
      </div>
      <p>{t("tasksHelp")}</p>
      <input
        className="as-search"
        aria-label={t("searchTask")}
        placeholder={t("searchTask")}
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setOffset(0);
        }}
      />
      {error && (
        <div className="as-notice" role="alert">
          {error}
        </div>
      )}
      {!current ? (
        <p role="status">{t(failed === key ? "loadFail" : "loading")}</p>
      ) : !current.rows.length ? (
        <div className="as-empty">{t(search ? "noMatches" : "noTasks")}</div>
      ) : (
        <>
          {current.rows.map((task) => {
            const outputs = Array.isArray(task.result_metadata?.outputs)
              ? (task.result_metadata.outputs as Array<{ objectKey?: string; mimeType?: string }>)
              : [];
            const uncertain = task.failure_metadata?.submission_unknown === true;
            return (
              <article className="as-job" key={task.id}>
                <div className="as-section-heading">
                  <h3>{task.resource_title || t(taskLabels[task.task_kind])}</h3>
                  <span className="as-pill">
                    {t(uncertain || !statusKeys.has(task.status) ? "unknownState" : task.status)}
                  </span>
                </div>
                <p>
                  {t(taskLabels[task.task_kind])}
                  {task.updated_at && " · " + new Date(task.updated_at).toLocaleString(i18n.resolvedLanguage)}
                </p>
                {task.billing_currency && (
                  <small>
                    {(
                      [
                        [task.estimated_minor, "amountEstimate"],
                        [task.actual_minor, "charged"],
                        [task.released_minor, "released"],
                      ] as const
                    )
                      .filter(([value, label]) => value != null && (label !== "released" || value > 0))
                      .map(([value, label]) =>
                        t(label, {
                          value: amount(value!, task.billing_currency!, i18n.resolvedLanguage ?? "zh"),
                        }),
                      )
                      .join(" · ")}
                  </small>
                )}
                {outputs
                  .filter((output) => output?.objectKey && output.mimeType?.startsWith("audio/"))
                  .map((output) => (
                    <AudioPreview
                      key={output.objectKey}
                      workspaceId={workspaceId}
                      projectId={projectId}
                      source={api.audioObjectUrl(projectId, output.objectKey!)}
                    />
                  ))}
                {uncertain && <p>{t("recovery")}</p>}
                {task.fallback_metadata?.strategy === "keep_original" && <p>{t("kept")}</p>}
                {canManage && !uncertain && (
                  <div className="as-actions">
                    {["pending", "queued", "running", "retrying"].includes(task.status) && (
                      <button
                        disabled={locked || busy}
                        className="as-button"
                        onClick={() => void action(task, true)}
                      >
                        {t("cancel")}
                      </button>
                    )}
                    {["failed", "timed_out"].includes(task.status) && (
                      <>
                        {task.failure_metadata?.retryable !== false &&
                          (task.attempt_count ?? 0) < (task.max_attempts ?? 1) && (
                            <button
                              className="as-button"
                              disabled={locked || busy}
                              onClick={() =>
                                submit({ action: "retry_media_task", taskId: task.id }, task.version)
                              }
                            >
                              {t("retry")}
                            </button>
                          )}
                        {!task.fallback_metadata?.strategy && (
                          <button
                            disabled={locked || busy}
                            className="as-button"
                            onClick={() => void action(task, false)}
                          >
                            {t("keep")}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </article>
            );
          })}
          {current.total > 10 && (
            <Pagination total={current.total} offset={offset} pageSize={10} onOffsetChange={setOffset} />
          )}
        </>
      )}
    </section>
  );
}
