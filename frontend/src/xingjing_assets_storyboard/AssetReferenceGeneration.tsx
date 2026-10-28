import type { AssetItem, TaskReceipt } from "./contracts";
import { useAssetGenerationTranslation } from "./asset-generation-i18n";
import { button, panel, primaryButton, TaskSummary } from "./ui";

export function assetHasReferenceImage(asset: AssetItem) {
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  return Boolean(version?.generatedAssetId && version.generatedMediaType === "image");
}

export function AssetReferenceGeneration({ asset, canManage, busy, draftBlocked = false, ownerProject = true, task, onGenerate, onAdopt }: {
  asset: AssetItem; canManage: boolean; busy: boolean; draftBlocked?: boolean; ownerProject?: boolean; task?: TaskReceipt;
  onGenerate: () => void; onAdopt: (generatedAssetId: string) => void;
}) {
  const { t } = useAssetGenerationTranslation();
  const version = asset.versions.find((item) => item.id === asset.currentVersionId);
  const hasReference = assetHasReferenceImage(asset);
  const working = Boolean(task && ["queued", "running", "retrying", "cancelling"].includes(task.status));
  const adoptedIds = new Set(asset.versions.map((item) => item.generatedAssetId).filter(Boolean));
  const blocked = busy || draftBlocked || !canManage || !ownerProject;
  return <section className={`${panel} mt-5 p-5`}>
    <h2 className="font-semibold">{t("title")}</h2>
    <p className="mt-2 text-sm leading-6 text-slate-400">{t("intro")}</p>
    {!ownerProject ? <p role="status" className="mt-3 text-xs leading-6 text-amber-200">{t("ownerProjectRequired")}</p> : null}
    {asset.type === "voice" ? <p className="mt-3 text-xs text-slate-400">{t("voice")}</p> : <>
      {hasReference && (version?.mediaUrl || version?.thumbnailUrl) ? <img className="mt-4 max-h-64 rounded-xl object-contain" src={version.mediaUrl || version.thumbnailUrl} alt={t("preview", { name: asset.name })} /> : null}
      <p className="mt-3 text-xs leading-6 text-slate-400">{!version ? t("sourceMissing") : hasReference ? t("sourceVersion", { number: version.versionNo }) : t("noImage")}</p>
      {!asset.description?.trim() ? <p className="mt-2 text-xs leading-6 text-slate-400">{t("noDescription")}</p> : null}
      {draftBlocked ? <p role="status" className="mt-3 text-xs text-amber-200">{t("savedFirst")}</p> : null}
      <button type="button" className={`${primaryButton} mt-4`} disabled={blocked || working || !version} onClick={onGenerate}>{t(busy ? "submitting" : hasReference ? "regenerate" : "generate")}</button>
    </>}
    {task ? <>
      <TaskSummary task={{ ...task, outputAssetIds: [] }} message={t("taskTitle")} />
      {working ? <p role="status" className="mt-3 text-xs text-violet-200">{t("waiting")}</p> : null}
      {["failed", "cancelled", "timeout"].includes(task.status) ? <p className="mt-3 text-xs text-amber-200">{t("failure")}</p> : null}
      {task.status === "succeeded" && task.outputAssetIds?.length ? <div className="mt-4">
        <h3 className="text-sm font-semibold">{t("candidateTitle")}</h3><p className="mt-2 text-xs leading-6 text-slate-400">{t("candidateHint")}</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">{task.outputAssetIds.map((id, index) => <div key={id} className="rounded-xl border border-white/10 p-3">
          {task.outputUrls?.[index] ? <img src={task.outputUrls[index]} alt={t("candidate", { name: asset.name, number: index + 1 })} className="aspect-square w-full rounded-lg object-contain" /> : <p className="text-sm">{t("candidate", { name: asset.name, number: index + 1 })}</p>}
          <button type="button" className={`${button} mt-3 w-full`} disabled={blocked || adoptedIds.has(id)} onClick={() => onAdopt(id)}>{t(adoptedIds.has(id) ? "adopted" : "adopt")}</button>
        </div>)}</div>
      </div> : null}
    </> : null}
  </section>;
}
