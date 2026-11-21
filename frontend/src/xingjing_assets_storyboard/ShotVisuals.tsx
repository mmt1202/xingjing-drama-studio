import { useEffect, useState } from "react";
import { Check, Image as ImageIcon, ImagePlus, RefreshCw, Video } from "lucide-react";
import { useAuthenticatedMedia } from "@/hooks/useAuthenticatedMedia";
import type { GenerationService } from "@/xingjing_generation_center/services";
import type { AssetsStoryboardPort, ShotItem, TaskReceipt, WorkspaceProjectScope } from "./contracts";
import { useBoardText } from "./board-studio-i18n";
import { useShotReviewTranslation } from "./shot-review-i18n";
import { BoardDialog } from "./StoryboardStudioDialog";

export type BoardMedium = "image" | "video";
export type ReferenceMode = "description" | "visual";
type Offer = GenerationService | "error" | undefined;
const activeStatuses: ReadonlySet<string> = new Set(["queued", "running", "retrying", "cancelling"]);

export function useBoardOffers(api: AssetsStoryboardPort, scope: WorkspaceProjectScope) {
  const { workspaceId, projectId } = scope;
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([workspaceId, projectId, revision]);
  const [offers, setOffers] = useState<{ key: string; image?: Offer; video?: Offer }>();
  useEffect(() => {
    if (!projectId || !api.getGenerationServices) return;
    const controller = new AbortController();
    for (const kind of ["image", "video"] as const) {
      const capability = kind === "image" ? "image_generation" : "video_generation";
      const put = (offer: Offer) => {
        if (!controller.signal.aborted)
          setOffers((last) => ({ ...(last?.key === key ? last : {}), key, [kind]: offer }));
      };
      void api.getGenerationServices({ workspaceId, projectId }, [capability], controller.signal, kind).then(
        (result) => put(result.find((entry) => entry.capability === capability) ?? "error"),
        () => put("error"),
      );
    }
    return () => controller.abort();
  }, [api, workspaceId, projectId, key]);
  return {
    forKind: (kind: BoardMedium): Offer =>
      !projectId || !api.getGenerationServices ? "error" : offers?.key === key ? offers[kind] : undefined,
    refresh: () => setRevision((value) => value + 1),
  };
}

function VisualPreview({
  source,
  scope,
  label,
}: {
  source: string;
  scope: WorkspaceProjectScope;
  label: string;
}) {
  const { t } = useBoardText();
  const media = useAuthenticatedMedia({
    source,
    workspaceId: scope.workspaceId,
    projectId: scope.projectId ?? "",
  });
  if (media.error)
    return (
      <div role="status" className="xj-board-notice">
        {t("previewFailed")}{" "}
        <button type="button" onClick={media.retry}>
          {t("retryPreview")}
        </button>
      </div>
    );
  if (!media.url)
    return (
      <p role="status" className="xj-board-notice">
        {t("previewLoad")}
      </p>
    );
  return media.mimeType?.startsWith("video/") ? (
    <video
      className="xj-board-media"
      controls
      preload="metadata"
      aria-label={label}
      onError={media.decodeFailed}
      src={media.url}
    >
      <track kind="captions" />
    </video>
  ) : (
    <img className="xj-board-media" src={media.url} alt={label} onError={media.decodeFailed} />
  );
}

function useCurrentTask(api: AssetsStoryboardPort, scope: WorkspaceProjectScope, taskId?: string) {
  const { workspaceId, projectId } = scope;
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ id: string; receipt?: TaskReceipt; failed?: boolean }>();
  useEffect(() => {
    if (!taskId || !projectId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const read = async () => {
      try {
        const receipt = await api.getGenerationTask(
          { workspaceId, projectId },
          { taskId, projectId, status: "queued" },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setState({ id: taskId, receipt });
        if (activeStatuses.has(receipt.status)) timer = window.setTimeout(() => void read(), 2200);
      } catch {
        if (!controller.signal.aborted) setState({ id: taskId, failed: true });
      }
    };
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [api, workspaceId, projectId, taskId, retry]);
  return { state: state?.id === taskId ? state : undefined, retry: () => setRetry((n) => n + 1) };
}

export function ShotVisuals({
  scope,
  api,
  shot,
  offers,
  canEdit,
  canGenerate,
  busy,
  submittedTask,
  onGenerate,
  onAdopt,
  onSelect,
}: {
  scope: WorkspaceProjectScope;
  api: AssetsStoryboardPort;
  shot: ShotItem;
  offers: ReturnType<typeof useBoardOffers>;
  canEdit: boolean;
  canGenerate: boolean;
  busy: boolean;
  submittedTask?: TaskReceipt;
  onGenerate: (media: BoardMedium, mode?: ReferenceMode) => void;
  onAdopt: (taskId: string, assetId: string) => void;
  onSelect: (id: string) => void;
}) {
  const { t, i18n } = useBoardText();
  const { t: review } = useShotReviewTranslation();
  const [kind, setKind] = useState<BoardMedium>("image");
  const [mode, setMode] = useState<ReferenceMode>("description");
  const [choice, setChoice] = useState<string>();
  const [confirmation, setConfirmation] = useState<{
    kind: BoardMedium;
    mode: ReferenceMode;
    version: number;
    quote: string;
  }>();
  const taskId = shot.generationTaskId ?? submittedTask?.taskId;
  const taskResult = useCurrentTask(api, scope, taskId);
  const task = taskResult.state?.receipt ?? (submittedTask?.taskId === taskId ? submittedTask : undefined);
  const candidates = shot.candidates.map((c) => ({
    id: c.id,
    label: t("candidate", { n: c.version }),
    selected: c.selected,
    source: c.mediaUrl || c.thumbnailUrl,
    generated: false,
  }));
  const outputs = task?.status === "succeeded" ? (task.outputAssetIds ?? []) : [];
  outputs.forEach((id, index) => {
    if (!candidates.some((c) => c.id === id))
      candidates.push({
        id,
        label: t("output", { n: index + 1 }),
        selected: false,
        source: task?.outputUrls?.[index],
        generated: true,
      });
  });
  const current =
    candidates.find((c) => c.id === choice) ?? candidates.find((c) => c.selected) ?? candidates[0];
  const offer = offers.forKind(kind);
  const references = shot.assetReferences.some((ref) => ref.type !== "voice");
  const useRefs = Boolean(references && offer && offer !== "error" && offer.acceptsReferences);
  const inputMode = useRefs ? mode : "description";
  const quoteMoney = (minor: number, currency: string) =>
    new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency }).format(minor / 100);
  const quote =
    offer &&
    offer !== "error" &&
    offer.available &&
    offer.currency &&
    typeof offer.estimatedMinor === "number"
      ? [
          t("hold", { amount: quoteMoney(offer.estimatedMinor, offer.currency) }),
          typeof offer.successfulOutputMinor === "number"
            ? t("charge", { amount: quoteMoney(offer.successfulOutputMinor, offer.currency) })
            : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : "";
  const status = task?.status ?? shot.generationStatus;
  const running = activeStatuses.has(status ?? "") || Boolean(taskId && !task && !taskResult.state);
  const canSubmit =
    canGenerate &&
    !busy &&
    !running &&
    !taskResult.state?.failed &&
    !shot.generationSubmissionPending &&
    !!shot.prompt?.trim() &&
    !!quote;
  let blockedKey: string | undefined;
  if (!canGenerate) blockedKey = "cannotGenerate";
  else if (!shot.prompt?.trim()) blockedKey = "needsDescription";
  else if (!offer) blockedKey = "checking";
  else if (offer === "error") blockedKey = "checkFailed";
  else if (!offer.available) blockedKey = kind === "image" ? "imageOff" : "videoOff";
  else if (!quote) blockedKey = "noPrice";
  const currentQuote =
    confirmation &&
    canSubmit &&
    confirmation.kind === kind &&
    confirmation.mode === inputMode &&
    confirmation.version === shot.version &&
    confirmation.quote === quote;

  return (
    <>
      <section className="xj-board-result" aria-label={t("results")}>
        {current ? (
          <>
            {current.source ? (
              <VisualPreview
                key={current.source}
                scope={scope}
                source={current.source}
                label={review("shot", { no: shot.shotNo }) + " · " + current.label}
              />
            ) : (
              <p className="xj-board-notice">{t("missingPreview")}</p>
            )}
            <div className="xj-board-result-tools">
              <div role="group" aria-label={t("results")} className="xj-board-options">
                {candidates.map((c) => (
                  <button
                    type="button"
                    key={c.id}
                    aria-pressed={current.id === c.id}
                    onClick={() => setChoice(c.id)}
                  >
                    {c.selected && <Check size={14} aria-hidden="true" />}
                    {c.label}
                  </button>
                ))}
              </div>
              {current.selected ? (
                <strong className="xj-board-selected">
                  <Check size={15} aria-hidden="true" />
                  {t("selected")}
                </strong>
              ) : (
                <button
                  type="button"
                  className="xj-board-action"
                  disabled={!canEdit || busy || (!current.source && shot.status === "frozen")}
                  onClick={() => {
                    if (current.generated && taskId) onAdopt(taskId, current.id);
                    else onSelect(current.id);
                  }}
                >
                  {t("adopt")}
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="xj-board-empty">
            <ImageIcon size={19} aria-hidden="true" />
            {t("first")}
          </div>
        )}
      </section>
      {taskResult.state?.failed && (
        <div className="xj-board-notice" role="status">
          {t("readTask")}{" "}
          <button type="button" onClick={taskResult.retry}>
            {t("refresh")}
          </button>
        </div>
      )}
      {status && status !== "not_started" && (
        <div className="xj-board-task" role="status">
          <strong>{t("status")}</strong> {review(status === "failed" ? "generationFailed" : status)}
          {running && task?.progress != null && <span> · {task.progress}%</span>}
          {status === "failed" && <p>{t("failed")}</p>}
          {task?.billingCurrency && task.actualCostMinor != null && (
            <p>{t("actual", { amount: quoteMoney(task.actualCostMinor, task.billingCurrency) })}</p>
          )}
        </div>
      )}
      {shot.generationSubmissionPending ? (
        <div className="xj-board-notice" role="status">
          <p>{t("pending")}</p>
          <button
            type="button"
            disabled={busy || !canGenerate || !shot.generationPendingMediaType}
            onClick={() => {
              if (shot.generationPendingMediaType) onGenerate(shot.generationPendingMediaType);
            }}
          >
            {t("resume")}
          </button>
        </div>
      ) : (
        <section className="xj-board-production">
          <div role="group" aria-label={t("outputKind")} className="xj-board-options">
            {(["image", "video"] as const).map((media) => (
              <button type="button" key={media} aria-pressed={kind === media} onClick={() => setKind(media)}>
                {media === "image" ? (
                  <ImageIcon size={15} aria-hidden="true" />
                ) : (
                  <Video size={15} aria-hidden="true" />
                )}
                {t(media)}
              </button>
            ))}
          </div>
          {blockedKey ? (
            <div className="xj-board-availability" role="status">
              <p>{t(blockedKey)}</p>
              {canGenerate && blockedKey !== "needsDescription" && blockedKey !== "checking" && (
                <button type="button" className="xj-board-link" onClick={offers.refresh}>
                  <RefreshCw size={14} aria-hidden="true" />
                  {t("refresh")}
                </button>
              )}
            </div>
          ) : (
            <>
              <div className="xj-board-options">
                {useRefs && (
                  <label>
                    {t("input")}{" "}
                    <select
                      value={inputMode}
                      disabled={busy || running}
                      onChange={(e) => setMode(e.target.value as ReferenceMode)}
                    >
                      <option value="description">{t("textOnly")}</option>
                      <option value="visual">{t("withRefs")}</option>
                    </select>
                  </label>
                )}
              </div>
              {inputMode === "visual" && <p>{t("refHelp")}</p>}
              <div className="xj-board-availability">
                <p>{quote}</p>
                <button
                  className="xj-board-primary"
                  type="button"
                  disabled={!canSubmit}
                  onClick={() => setConfirmation({ kind, mode: inputMode, version: shot.version, quote })}
                >
                  <ImagePlus size={16} aria-hidden="true" />
                  {t(kind === "image" ? "makeImage" : "makeVideo")}
                </button>
              </div>
            </>
          )}
        </section>
      )}
      {confirmation && (
        <BoardDialog
          title={t("generateTitle", { no: shot.shotNo, kind: t(confirmation.kind) })}
          detail={
            currentQuote
              ? t("generateDetail", {
                  mode: t(confirmation.mode === "visual" ? "withRefs" : "textOnly"),
                  quote,
                })
              : t("conflictBody")
          }
          label={t("submit")}
          disabled={!currentQuote}
          onClose={() => setConfirmation(undefined)}
          onConfirm={() => {
            if (currentQuote) onGenerate(confirmation.kind, confirmation.mode);
            setConfirmation(undefined);
          }}
        />
      )}
    </>
  );
}
