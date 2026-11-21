import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Clock3,
  ListVideo,
  Pencil,
} from "lucide-react";
import { ShotAssets, assetDisplayName } from "./ShotReviewList";
import { type BoardMedium, type ReferenceMode, ShotVisuals, useBoardOffers } from "./ShotVisuals";
import { BoardDialog } from "./StoryboardStudioDialog";
import { AssetsStoryboardApiError } from "./api";
import { useBoardText } from "./board-studio-i18n";
import type { AssetsStoryboardPort, ShotItem, TaskReceipt, WorkspaceProjectScope } from "./contracts";
import { useShotReviewTranslation } from "./shot-review-i18n";
import { SHOTS_PER_PAGE } from "./useShotCollection";
import "./storyboard-studio.css";

interface StudioProps {
  scope: WorkspaceProjectScope;
  api: AssetsStoryboardPort;
  shots: readonly ShotItem[];
  order: readonly string[];
  frozen: boolean;
  canManage: boolean;
  canGenerate: boolean;
  busy: boolean;
  total: number;
  offset: number;
  version: number;
  selectedFrames?: number;
  pagination: ReactNode;
  submittedTask?: TaskReceipt;
  submittedShotId?: string;
  onPage: (offset: number) => void;
  onEdit: (shot: ShotItem) => void;
  onGenerate: (shot: ShotItem, kind: BoardMedium, mode?: ReferenceMode) => void;
  onSelect: (shot: ShotItem, id: string) => void;
  onAdopt: (shot: ShotItem, taskId: string, assetId: string) => void;
  onConfirm: () => void;
  onNext?: () => void;
}

export function BoardActionNotice({
  error,
  onReload,
  onQuality,
  onAssets,
}: {
  error: unknown;
  onReload: () => void;
  onQuality: () => void;
  onAssets?: () => void;
}) {
  const { t } = useBoardText();
  const { t: review } = useShotReviewTranslation();
  const cause = error instanceof AssetsStoryboardApiError ? error : undefined;
  const code = cause?.code ?? "";
  const stale = code === "VERSION_CONFLICT";
  const needsAssets =
    code.startsWith("GENERATION_INPUT_") || code === "GENERATION_PROVIDER_INPUT_CONTRACT_UNAVAILABLE";
  const quality = code === "QUALITY_GATE_FAILED" || code === "QUALITY_POLICY_REQUIRED";
  let detail = review("actionFailed");
  if (stale) detail = t("conflictBody");
  else if (code.startsWith("STORYBOARD_FROZEN")) detail = t("frozenError");
  else if (code === "QUALITY_GATE_FAILED") detail = t("localGate");
  else if (code === "QUALITY_POLICY_REQUIRED") detail = t("qualityUnavailable");
  else if (needsAssets) detail = t("refsMissing");
  else if (["GENERATION_INSUFFICIENT_CREDITS", "GENERATION_BILLING_ACCOUNT_MISSING"].includes(code))
    detail = t("credits");
  else if (
    ["SHOT_GENERATION_SUBMISSION_PENDING", "GENERATION_SUBMISSION_VERIFICATION_REQUIRED"].includes(code)
  )
    detail = t("pending");
  else if (code === "SHOT_PROMPT_REQUIRED") detail = t("needsDescription");
  else if (code === "GENERATION_WORKSPACE_CONCURRENCY_LIMIT") detail = t("queueFull");
  else if (cause?.status === 403) detail = t("viewDenied");
  else if (code.startsWith("MODEL_") || code === "GENERATION_PROVIDER_SUBMISSION_UNAVAILABLE")
    detail = t("serviceOff");
  let recovery = onReload;
  let recoveryLabel = t(stale || code.startsWith("STORYBOARD_FROZEN") ? "loadLatest" : "refresh");
  if (quality) {
    recovery = onQuality;
    recoveryLabel = t("checkQuality");
  } else if (needsAssets && onAssets) {
    recovery = onAssets;
    recoveryLabel = t("openAssets");
  }
  return (
    <section
      className="mb-4 rounded-xl border border-amber-300/25 bg-amber-100/[.055] p-4 text-sm leading-6 text-amber-100"
      role="alert"
    >
      <strong>{t(stale ? "conflict" : "couldNot")}</strong>
      <p>{detail}</p>
      <button type="button" className="xj-board-action mt-2" onClick={recovery}>
        {recoveryLabel}
      </button>
      {cause?.requestId && (
        <details className="mt-2 text-xs text-slate-400">
          <summary className="w-fit cursor-pointer">{t("support")}</summary>
          <code className="break-all">{cause.requestId}</code>
        </details>
      )}
    </section>
  );
}

export function StoryboardStudio(props: StudioProps) {
  const { shots, order, scope, api, total, offset, frozen, canManage, canGenerate, busy, onEdit } = props;
  const { t, i18n } = useBoardText();
  const { t: review } = useShotReviewTranslation();
  const positionKey =
    "storyboard-focus:" + JSON.stringify([scope.workspaceId, scope.projectId, shots[0].storyboardId]);
  const [focusId, setFocusId] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(positionKey);
    } catch {
      return null;
    }
  });
  const [confirmVersion, setConfirmVersion] = useState<number>();
  const offers = useBoardOffers(api, scope);
  const shot = shots.find((s) => s.id === focusId) ?? shots[0];
  const position = order.indexOf(shot.id);
  const seconds = new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 3 });
  const duration = (s: ShotItem) => review("sec", { n: seconds.format(s.durationMs / 1000) });
  function openShot(id: string) {
    try {
      sessionStorage.setItem(positionKey, id);
    } catch {
      /* Keep the current selection in memory. */
    }
    setFocusId(id);
    const index = order.indexOf(id);
    if (index >= 0 && (index < offset || index >= offset + shots.length))
      props.onPage(Math.floor(index / SHOTS_PER_PAGE) * SHOTS_PER_PAGE);
  }
  return (
    <div className="xj-board-studio">
      <div className="xj-board-grid">
        <nav className="xj-board-index" aria-label={t("index")}>
          <div className="xj-board-section-heading">
            <ListVideo size={18} aria-hidden="true" />
            <h2>{t("index")}</h2>
            <span>{total}</span>
          </div>
          <ol start={offset + 1}>
            {shots.map((item) => {
              const scene = item.assetReferences
                .filter((ref) => ref.type === "scene")
                .map(assetDisplayName)
                .find(Boolean);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-current={item.id === shot.id ? "step" : undefined}
                    disabled={busy}
                    aria-label={t("selectShot", { no: item.shotNo })}
                    onClick={() => openShot(item.id)}
                  >
                    <span className="xj-board-row-top">
                      <strong>{item.shotNo}</strong>
                      <span>{duration(item)}</span>
                      {item.candidates.some((c) => c.selected) && <Check size={14} aria-label={t("ready")} />}
                    </span>
                    <span className="xj-board-row-note">
                      {[scene, item.shotSize].filter(Boolean).join(" · ") ||
                        item.prompt ||
                        review("noCamera")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          {props.pagination}
        </nav>
        <article className="xj-board-detail" aria-label={review("shot", { no: shot.shotNo })}>
          <header className="xj-board-detail-head">
            <div>
              <div className="xj-board-section-heading">
                <Clapperboard size={19} aria-hidden="true" />
                <h2>{review("shot", { no: shot.shotNo })}</h2>
              </div>
              <p>
                {[shot.shotSize, shot.cameraMove].filter(Boolean).join(" · ")}{" "}
                <span>
                  <Clock3 size={13} aria-hidden="true" />
                  {duration(shot)}
                </span>
              </p>
            </div>
            <button type="button" className="xj-board-action" disabled={busy} onClick={() => onEdit(shot)}>
              <Pencil size={14} aria-hidden="true" />
              {review(canManage && !frozen ? "editNamed" : "review", { no: shot.shotNo })}
            </button>
          </header>
          <section className="xj-board-script">
            <h3>{t("description")}</h3>
            <p>{shot.prompt || t("absent")}</p>
            {shot.dialogue && (
              <details>
                <summary>{t("lines")}</summary>
                <p>{shot.dialogue}</p>
              </details>
            )}
            <ShotAssets shot={shot} />
            {shot.issueCount > 0 && (
              <p className="xj-board-issues">{review("issues", { n: shot.issueCount })}</p>
            )}
          </section>
          <ShotVisuals
            key={shot.id}
            scope={scope}
            api={api}
            shot={shot}
            offers={offers}
            busy={busy}
            canEdit={canManage}
            canGenerate={canGenerate}
            submittedTask={props.submittedShotId === shot.id ? props.submittedTask : undefined}
            onGenerate={(kind, mode) => props.onGenerate(shot, kind, mode)}
            onSelect={(id) => props.onSelect(shot, id)}
            onAdopt={(taskId, id) => props.onAdopt(shot, taskId, id)}
          />
          <div className="xj-board-step">
            <button
              className="xj-board-action"
              type="button"
              disabled={busy || position <= 0}
              onClick={() => openShot(order[position - 1])}
            >
              <ChevronLeft size={16} aria-hidden="true" />
              {t("previous")}
            </button>
            <span>
              {position + 1} / {total}
            </span>
            <button
              className="xj-board-action"
              type="button"
              disabled={busy || position < 0 || position >= order.length - 1}
              onClick={() => openShot(order[position + 1])}
            >
              {t("following")}
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
      <section className="xj-board-final">
        <div>
          <strong>{review(frozen ? "frozen" : "plan")}</strong>
          {props.selectedFrames != null && (
            <span>{t("pickedProgress", { n: props.selectedFrames, total })}</span>
          )}
          <p>{t(frozen ? "frozenHelp" : "draftHelp")}</p>
        </div>
        {frozen
          ? props.onNext && (
              <button type="button" className="xj-board-primary" disabled={busy} onClick={props.onNext}>
                {t("next")}
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            )
          : canManage && (
              <button
                type="button"
                className="xj-board-action"
                disabled={busy}
                onClick={() => setConfirmVersion(props.version)}
              >
                {review("freeze")}
              </button>
            )}
      </section>
      {confirmVersion !== undefined && (
        <BoardDialog
          title={t("freezeTitle")}
          detail={confirmVersion === props.version ? t("freezeDetail", { n: total }) : t("conflictBody")}
          label={t("freezeConfirm", { n: confirmVersion })}
          disabled={busy || frozen || confirmVersion !== props.version}
          onClose={() => setConfirmVersion(undefined)}
          onConfirm={() => {
            setConfirmVersion(undefined);
            props.onConfirm();
          }}
        />
      )}
    </div>
  );
}
