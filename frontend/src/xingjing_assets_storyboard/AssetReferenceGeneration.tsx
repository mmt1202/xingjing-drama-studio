import type { AssetItem, TaskReceipt } from "./contracts";
import { useAssetGenerationTranslation } from "./asset-generation-i18n";
import { button, panel, primaryButton, TaskSummary } from "./ui";
import { useAuthenticatedMedia } from "@/hooks/useAuthenticatedMedia";
import type { AssetImageControls } from "./asset-image-models";

export function assetHasReferenceImage(asset: AssetItem) {
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  return Boolean(version?.generatedAssetId && version.generatedMediaType === "image");
}

function ReferencePreview({ source, alt, workspaceId, projectId }: { source: string; alt: string; workspaceId: string; projectId: string }) {
  const { t } = useAssetGenerationTranslation();
  const media = useAuthenticatedMedia({ source, workspaceId, projectId });
  if (media.error) return <div className="mt-3 text-sm text-amber-200" role="status">
    {t("previewFailed")} <button type="button" className={button} onClick={media.retry}>{t("retryImage")}</button>
  </div>;
  return media.url ? <img src={media.url} alt={alt} onError={media.decodeFailed} className="mt-3 max-h-64 w-full rounded-xl object-contain" /> :
    <p role="status" className="mt-2 text-xs text-slate-400">{t("loadingImage")}</p>;
}

export function AssetReferenceGeneration({ asset, canManage, busy, generation, draftBlocked = false, ownerProject = true, task, onGenerate, onAdopt }: {
  asset: AssetItem; canManage: boolean; busy: boolean; draftBlocked?: boolean; ownerProject?: boolean; task?: TaskReceipt;
  generation: AssetImageControls;
  onGenerate: () => void; onAdopt: (generatedAssetId: string) => void;
}) {
  const { t, i18n } = useAssetGenerationTranslation();
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  const hasReference = assetHasReferenceImage(asset);
  const working = Boolean(task && ["queued", "running", "retrying", "cancelling"].includes(task.status));
  const adoptedIds = new Set(asset.versions.map((item) => item.generatedAssetId).filter(Boolean));
  const blocked = busy || generation.pending || draftBlocked || !canManage || !ownerProject;
  const { models } = generation;
  const ready = models.supports("image_generation");
  const offer = models.offer("image_generation");
  const projectId = asset.projectId ?? models.projectId;
  const source = hasReference && projectId && version?.generatedAssetId
    ? `/api/v1/generation-assets/${encodeURIComponent(version.generatedAssetId)}/content?projectId=${encodeURIComponent(projectId)}` : undefined;
  return <section className={`${panel} mt-5 p-5`}>
    <h2 className="font-semibold">{t("title")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("intro")}</p>
    {!ownerProject ? <p role="status" className="mt-3 text-xs leading-6 text-amber-200">{t("ownerProjectRequired")}</p> : null}
    {asset.type === "voice" ? <p className="mt-3 text-xs text-slate-400">{t("voice")}</p> : <>
      {source && projectId ? <ReferencePreview source={source} workspaceId={models.workspaceId} projectId={projectId} alt={t("preview", { name: asset.name })} /> : null}
      <p className="mt-3 text-xs leading-6 text-slate-400">{!version ? t("sourceMissing") : hasReference ? t("sourceVersion", { number: version.versionNo }) : t("plainAsset")}</p>
      {!asset.description?.trim() ? <p className="mt-2 text-xs leading-6 text-slate-400">{t("noDescription")}</p> : null}
      {draftBlocked ? <p role="status" className="mt-3 text-xs text-amber-200">{t("savedFirst")}</p> : null}
      {generation.rejection ? <p role="status" className="mt-3 text-sm text-amber-200">{generation.rejection} {generation.billingHref ? <a className="underline" href={generation.billingHref}>{t(generation.canManageBilling ? "topUpLink" : "balanceLink")}</a> : null}</p> : null}
      {ownerProject && !ready ? <div className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/5 p-3">
        <p role="status" className="text-sm leading-6 text-amber-100">{t(models.notice ?? "modelMissing")}</p>
        {models.notice === "checking" ? null : <div className="mt-2 flex flex-wrap items-center gap-3">
          <button type="button" className={button} onClick={models.recheck}>{t("checkModels")}</button>
        </div>}
      </div> : null}
      {ready && offer?.currency && offer.estimatedMinor != null ? <p className="mt-3 text-sm text-slate-300">
        {t("estimate", { amount: new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: offer.currency }).format(offer.estimatedMinor / 100) })}
        {offer.successfulOutputMinor != null ? ` ${t("charge", { amount: new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: offer.currency }).format(offer.successfulOutputMinor / 100) })}` : null}
      </p> : null}
      <button type="button" className={`${primaryButton} mt-4 disabled:cursor-not-allowed disabled:opacity-50`} disabled={blocked || working || !version || !ready} onClick={onGenerate}>{t(generation.activeAction === "generateReferenceImage" ? "submitting" : hasReference ? "regenerate" : "generate")}</button>
    </>}
    {task ? <>
      <TaskSummary task={{ ...task, outputAssetIds: [] }} message={t("taskTitle")} />
      {working ? <p role="status" className="mt-3 text-xs text-violet-200">{t("waiting")}</p> : null}
      {["failed", "cancelled", "timeout"].includes(task.status) ? <p className="mt-3 text-xs text-amber-200">{t("failure")}</p> : null}
      {task.status === "succeeded" && task.outputAssetIds?.length ? <div className="mt-4">
        <h3 className="text-sm font-semibold">{t("candidateTitle")}</h3><p className="mt-2 text-xs leading-6 text-slate-400">{t("candidateHint")}</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">{task.outputAssetIds.map((id, index) => <div key={id} className="rounded-xl border border-white/10 p-3">
          {task.outputUrls?.[index] && projectId ? <ReferencePreview source={task.outputUrls[index]} workspaceId={models.workspaceId} projectId={projectId} alt={t("candidate", { name: asset.name, number: index + 1 })} /> : <p className="text-sm">{t("candidate", { name: asset.name, number: index + 1 })}</p>}
          <button type="button" className={`${button} mt-3 w-full`} disabled={blocked || adoptedIds.has(id)} onClick={() => onAdopt(id)}>{t(adoptedIds.has(id) ? "adopted" : "adopt")}</button>
        </div>)}</div>
      </div> : null}
    </> : null}
  </section>;
}
