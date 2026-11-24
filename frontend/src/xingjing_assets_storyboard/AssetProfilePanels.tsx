import { useState } from "react";
import { ArrowUpRight, BookImage, ChevronDown, History, PencilRuler } from "lucide-react";
import { useQueueText } from "@/xingjing_generation_center/queue-i18n";
import { ReferencePreview, assetHasReferenceImage } from "./AssetReferenceGeneration";
import { profileLabel, useAssetProfileTranslation } from "./asset-profile-i18n";
import { useAssetEditorTranslation } from "./editor-i18n";
import type { AssetItem, AssetVersion, TaskReceipt } from "./contracts";
import { ConfirmDialog } from "./ui";

export function CharacterArtworkLink({ asset, workspaceId, projectId, task, blocked, savingAllowed, onSaveAndOpen, onOpen }: {
  asset: AssetItem; workspaceId: string; projectId?: string; task?: TaskReceipt; blocked: boolean;
  savingAllowed: boolean; onSaveAndOpen: () => void;
  onOpen: (view: "turnaround" | "expressions") => void;
}) {
  const { t } = useAssetEditorTranslation();
  const { t: queue } = useQueueText();
  const current = asset.versions.find((version) => version.id === asset.currentVersionId);
  const hasImage = assetHasReferenceImage(asset);
  const imageId = hasImage && current?.generatedAssetId;
  const source = imageId && projectId && `/api/v1/generation-assets/${encodeURIComponent(imageId)}/content?projectId=${encodeURIComponent(projectId)}`;
  const working = task && ["queued", "retrying", "running", "cancelling"].includes(task.status);
  const candidates = task?.status === "succeeded" && task.outputAssetIds?.some((id) => !asset.versions.some((v) => v.generatedAssetId === id));
  return <section className="xj-profile-side xj-profile-art">
    <h2><PencilRuler size={18} aria-hidden="true" />{t("drawing")}</h2>
    {source && projectId && <ReferencePreview key={source} source={source} workspaceId={workspaceId} projectId={projectId} alt={t("alt", { name: asset.name })} />}
    <p>{t(hasImage ? "artReady" : "artEmpty")}</p>
    {(working || candidates) && <div className="xj-profile-task" role="status">
      <p>{t(working ? task?.waitingStopped ? "stopped" : "working" : "review")}</p>
      {task && projectId && <a href={`/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}/tasks?taskId=${encodeURIComponent(task.taskId)}`} target="_blank" rel="noopener noreferrer">{queue("detail")}<ArrowUpRight size={13} aria-hidden="true" /></a>}
    </div>}
    <div className="xj-profile-actions">
      <button type="button" className="xj-lib-btn" disabled={blocked && !savingAllowed} onClick={savingAllowed ? onSaveAndOpen : () => onOpen("turnaround")}>
        <BookImage aria-hidden="true" size={16} />{t(savingAllowed ? "saveBoard" : "board")}
      </button>
      {hasImage && <button type="button" className="xj-lib-textbtn" disabled={blocked} onClick={() => onOpen("expressions")}>{t("expressions")}</button>}
    </div>
    {blocked && !savingAllowed && <small>{t("saveBefore")}</small>}
  </section>;
}

export function AssetProfileHistory({ asset, canEdit, busy, dirty, onRestore }: {
  asset: AssetItem; canEdit: boolean; busy: boolean; dirty: boolean;
  onRestore: (versionId: string) => void;
}) {
  const { t, i18n } = useAssetEditorTranslation();
  const { t: field } = useAssetProfileTranslation();
  const [choice, setChoice] = useState<AssetVersion>();
  const versions = [...asset.versions].sort((a, b) => b.versionNo - a.versionNo);
  const disabled = !canEdit || busy || dirty;
  return <details className="xj-profile-history">
    <summary><span><History size={17} aria-hidden="true" />{t("history")}</span><ChevronDown size={15} aria-hidden="true" /></summary>
    <ol>{versions.map((version) => {
      const current = version.id === asset.currentVersionId;
      const date = new Date(version.createdAt);
      const details = Object.entries(version.profile ?? {}).filter(([key]) => key !== "description");
      return <li key={version.id}>
        <div className="xj-profile-version"><strong>{t("version", { number: version.versionNo })}</strong>{current && <span>{t("inUse")}</span>}</div>
        {Number.isFinite(date.valueOf()) && <time dateTime={version.createdAt}>{date.toLocaleString(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short" })}</time>}
        {version.profile && <p>{version.profile.description || (!details.length && t("noText"))}</p>}
        {details.length > 0 && <dl>{details.map(([key, value]) => <div key={key}><dt>{field(profileLabel(asset.type, key))}</dt><dd>{value}</dd></div>)}</dl>}
        {version.generatedMediaType === "image" && <p>{t("hasArt")}</p>}
        {!current && canEdit && <button type="button" className="xj-lib-btn" disabled={disabled} onClick={() => setChoice(version)}>{t("useVersion")}</button>}
      </li>;
    })}</ol>
    {dirty && versions.length > 1 && <small>{t("saveBefore")}</small>}
    {choice && <ConfirmDialog open title={t("restoreTitle", { number: choice.versionNo })} detail={t("restoreDetail")} confirmLabel={t("useVersion")} busy={disabled}
      onCancel={() => setChoice(undefined)} onConfirm={() => { if (!disabled) onRestore(choice.id); setChoice(undefined); }} />}
  </details>;
}
