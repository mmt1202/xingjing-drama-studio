import { useEffect, useState } from "react";
import type { AssetItem, GenerationCreditBalance, TaskReceipt } from "./contracts";
import { useAssetGenerationTranslation } from "./asset-generation-i18n";
import { button, panel, primaryButton } from "./ui";
import { useQueueText } from "@/xingjing_generation_center/queue-i18n";
import { useAuthenticatedMedia } from "@/hooks/useAuthenticatedMedia";
import type { AssetImageCapability, AssetImageControls } from "./asset-image-models";

export function assetHasReferenceImage(asset: AssetItem) {
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  return Boolean(version?.generatedAssetId && version.generatedMediaType === "image");
}

export function ReferencePreview({ source, alt, workspaceId, projectId, className }: { source: string; alt: string; workspaceId: string; projectId: string; className?: string }) {
  const { t } = useAssetGenerationTranslation();
  const media = useAuthenticatedMedia({ source, workspaceId, projectId });
  if (media.error) return <div className="mt-3 text-sm text-amber-200" role="status">
    {t("previewFailed")} <button type="button" className={button} onClick={media.retry}>{t("retryImage")}</button>
  </div>;
  return media.url ? <img src={media.url} alt={alt} onError={media.decodeFailed} className={className ?? "mt-3 max-h-64 w-full rounded-xl object-contain"} /> :
    <p role="status" className="mt-2 text-xs text-slate-400">{t("loadingImage")}</p>;
}

function imagePrice(minor: number, currency: string, language: string | undefined) {
  const formatter = new Intl.NumberFormat(language, { style: "currency", currency });
  return formatter.format(minor / 10 ** (formatter.resolvedOptions().maximumFractionDigits ?? 2));
}

export function AssetImageBilling({ generation, capability }: { generation: AssetImageControls; capability: AssetImageCapability }) {
  const { t, i18n } = useAssetGenerationTranslation();
  const { models, getCreditBalance } = generation;
  const offer = models.offer(capability);
  const workspaceId = models.workspaceId;
  const canView = offer?.canViewBilling === true;
  const rejected = generation.creditRejection === capability;
  const [expanded, setExpanded] = useState(false);
  const [balance, setBalance] = useState<GenerationCreditBalance | null>();
  const showBalance = canView && (expanded || rejected);

  useEffect(() => {
    if (!showBalance || !getCreditBalance) return;
    const controller = new AbortController();
    void getCreditBalance(workspaceId, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setBalance(value); },
      () => { if (!controller.signal.aborted) setBalance(null); },
    );
    return () => controller.abort();
  }, [showBalance, rejected, getCreditBalance, workspaceId]);

  return <>
    {models.supports(capability) && offer?.currency && offer.estimatedMinor != null ? <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm text-slate-300">
      <p>{t(offer.successfulOutputMinor === 0 ? "freeReserve" : "estimate", { amount: imagePrice(offer.estimatedMinor, offer.currency, i18n.resolvedLanguage) })}
        {offer.successfulOutputMinor != null && offer.successfulOutputMinor > 0 ? ` ${t("charge", { amount: imagePrice(offer.successfulOutputMinor, offer.currency, i18n.resolvedLanguage) })}` : null}</p>
      {canView && !rejected ? <button type="button" aria-expanded={expanded} className="text-violet-300 underline underline-offset-2" onClick={() => { setBalance(undefined); setExpanded(!expanded); }}>{t(expanded ? "hideBalance" : "showBalance")}</button> : null}
    </div> : null}
    {rejected || showBalance ? <div role="status" className={`mt-3 space-y-1 rounded-lg border px-3 py-2 text-sm leading-6 ${rejected ? "border-amber-300/20 bg-amber-300/5 text-amber-100" : "border-white/10 text-slate-300"}`}>
      {rejected ? <p>{t("insufficient")}</p> : null}
      {showBalance && (balance ? <p>{t("balanceAmounts", {
        available: imagePrice(balance.availableMinor, balance.currency, i18n.resolvedLanguage),
        held: imagePrice(balance.heldMinor, balance.currency, i18n.resolvedLanguage),
      })} {rejected && balance.heldMinor > 0 ? t("heldHint") : null}</p> : <p>{t(balance === undefined && getCreditBalance ? "balanceLoading" : "balanceUnavailable")}</p>)}
      {rejected && offer && !offer.canManageBilling ? <p>{t("askOwner")}</p> : null}
      {showBalance ? <a className="inline-block underline underline-offset-2" href={`/app/workspaces/${encodeURIComponent(workspaceId)}/billing`}>{t("balanceLink")}</a> : null}
    </div> : null}
  </>;
}

export function AssetReferenceGeneration({ asset, canManage, busy, generation, draftBlocked = false, ownerProject = true, profileEditor = false, task, onGenerate, onAdopt }: {
  asset: AssetItem; canManage: boolean; busy: boolean; draftBlocked?: boolean; ownerProject?: boolean; task?: TaskReceipt;
  generation: AssetImageControls; profileEditor?: boolean;
  onGenerate: () => void; onAdopt: (generatedAssetId: string) => void;
}) {
  const { t } = useAssetGenerationTranslation();
  const { t: queue } = useQueueText();
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  const hasReference = assetHasReferenceImage(asset);
  const working = Boolean(task && ["queued", "running", "retrying", "cancelling"].includes(task.status));
  const adoptedIds = new Set(asset.versions.map((item) => item.generatedAssetId).filter(Boolean));
  const blocked = busy || generation.pending || draftBlocked || !canManage || !ownerProject;
  const { models } = generation;
  const ready = models.supports("image_generation");
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
      {profileEditor && canManage && ownerProject && !asset.description?.trim() ? <p className="mt-2 text-xs leading-6 text-slate-400">{t("noDescription")}</p> : null}
      {draftBlocked ? <p role="status" className="mt-3 text-xs text-amber-200">{t("savedFirst")}</p> : null}
      {generation.rejection ? <p role="status" className="mt-3 text-sm text-amber-200">{generation.rejection}</p> : null}
      {ownerProject && !ready ? <div className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/5 p-3">
        <p role="status" className="text-sm leading-6 text-amber-100">{t(models.notice ?? "modelMissing")}</p>
        {models.notice === "checking" ? null : <div className="mt-2 flex flex-wrap items-center gap-3">
          <button type="button" className={button} onClick={models.recheck}>{t("checkModels")}</button>
        </div>}
      </div> : null}
      <AssetImageBilling key={asset.id} generation={generation} capability="image_generation" />
      <button type="button" className={`${primaryButton} mt-4 disabled:cursor-not-allowed disabled:opacity-50`} disabled={blocked || working || !version || !ready} onClick={onGenerate}>{t(generation.activeAction === "generateReferenceImage" ? "submitting" : hasReference ? "regenerate" : "generate")}</button>
    </>}
    {task ? <>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
        <strong>{t("taskTitle")}</strong>
        <span>{queue(task.waitingStopped ? "waiting_stopped" : working && task.executionUnconfirmed ? "checking" : task.status)}</span>
        {projectId && <a className="text-violet-200 underline" href={`/app/workspaces/${encodeURIComponent(models.workspaceId)}/projects/${encodeURIComponent(projectId)}/tasks?taskId=${encodeURIComponent(task.taskId)}`}>{queue("detail")}</a>}
      </div>
      {working ? <p role="status" className="mt-3 text-xs text-violet-200">{task.waitingStopped ? queue("stoppedHelp") : task.executionUnconfirmed ? queue("checkHelp") : t("waiting")}</p> : null}
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
