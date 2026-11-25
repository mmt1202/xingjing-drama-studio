import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { CheckCheck, Images, ShieldCheck, X } from "lucide-react";
import { useAssetLibraryTranslation } from "./asset-library-i18n";
import { useAssetImageModels } from "./asset-image-models";
import { batchEligible, batchError, submitAssetImage, type AssetBatchMode } from "./asset-batch";
import type { AssetItem, AssetsStoryboardPort, RightsEvidenceUpload, WorkspaceProjectScope } from "./contracts";
import { useDraftProtection } from "./useDraftProtection";
import { LicenseProofField, LicenseTermField, type LicenseEnd } from "./LicenseFields";

type RowResult = { label: string; failed?: boolean; asset?: AssetItem; taskId?: string };
type Props = {
  mode: AssetBatchMode;
  assets: readonly AssetItem[];
  scope: WorkspaceProjectScope;
  actorId: string;
  api: AssetsStoryboardPort;
  onClose: (saved: AssetItem[]) => void;
};

export function AssetBatchDialog({ mode, assets, scope, api, actorId, onClose }: Props) {
  const { t, i18n } = useAssetLibraryTranslation();
  const [items] = useState(() => assets.filter((asset) => batchEligible(mode, asset)));
  const [keys] = useState(() => new Map(items.map((asset) => [asset.id, crypto.randomUUID()])));
  const [rows, setRows] = useState<Record<string, RowResult>>({});
  const [busy, setBusy] = useState(false);
  const [holder, setHolder] = useState("");
  const [licenseScope, setLicenseScope] = useState("owner_project_only");
  const [validTo, setValidTo] = useState<LicenseEnd>(() => items.some((asset) => asset.rightsStatus === "expired") ? "" : null);
  const [file, setFile] = useState<File>();
  const [attested, setAttested] = useState(false);
  const [notice, setNotice] = useState("");
  const proofs = useRef(new Map<string, Promise<RightsEvidenceUpload>>());
  const latch = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const isImage = mode === "imageMany";
  const isRegistration = mode === "registerMany";
  const models = useAssetImageModels(api, scope.workspaceId, isImage ? scope.projectId : undefined);
  const offer = models.offer("image_generation");
  const targets = items.filter((item) => !rows[item.id] || rows[item.id].failed);
  const started = Object.keys(rows).length > 0;
  const maySubmit = !busy && targets.length > 0 && (isImage ? offer?.available : attested)
    && (!isRegistration || Boolean(holder.trim()));
  const Icon = isImage ? Images : isRegistration ? ShieldCheck : CheckCheck;
  useDraftProtection(busy);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  function close() {
    if (!latch.current) onClose(Object.values(rows).flatMap((row) => row.asset ? [row.asset] : []));
  }
  function setRow(asset: AssetItem, result: RowResult) {
    setRows((current) => ({ ...current, [asset.id]: result }));
  }
  function proofFor(asset: AssetItem, projectId: string, attachment: File) {
    const existing = proofs.current.get(projectId);
    if (existing) return existing;
    const upload = api.uploadRightsEvidence({ ...scope, projectId }, asset, attachment);
    proofs.current.set(projectId, upload);
    return upload;
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (latch.current || !maySubmit) return;
    latch.current = true;
    setBusy(true);
    setNotice("");
    let next = 0;
    async function worker() {
      while (next < targets.length) {
        const asset = targets[next++];
        setRow(asset, { label: "batchSending" });
        try {
          if (isImage) {
            const result = await submitAssetImage(api, scope, actorId, asset);
            setRow(asset, { ...result, failed: result.label === "batchRecover" });
          } else {
            const projectId = scope.projectId ?? asset.projectId;
            if (!projectId) throw new Error("missing owner project");
            const proof = isRegistration && file ? await proofFor(asset, projectId, file) : undefined;
            const result = await api.assetAction({ ...scope, projectId }, {
              action: isRegistration ? "saveRights" : "confirmRights",
              targetId: asset.id, version: asset.version, idempotencyKey: keys.get(asset.id),
              payload: isRegistration
                ? { ownerProjectId: projectId, holder: holder.trim(), licenseScope, validTo: validTo || undefined,
                    ...(proof ? { proofObjectKey: proof.objectKey, proofSha256: proof.sha256 } : {}) }
                : { ownerProjectId: projectId, rightsRecordId: asset.rights?.id },
            });
            if (!result.asset) throw new Error("missing record");
            setRow(asset, { label: isRegistration ? "pending" : "verified", asset: result.asset });
          }
        } catch (error) {
          setRow(asset, { label: batchError(error), failed: true });
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, targets.length) }, worker));
    } finally {
      // A failed upload may be retried. Keep successful project-scoped receipts.
      for (const [projectId, proof] of proofs.current) void proof.catch(() => proofs.current.delete(projectId));
      latch.current = false;
      setBusy(false);
    }
  }
  async function download(asset: AssetItem) {
    if (!api.downloadRightsEvidence || !asset.rights?.id || !asset.rights.proofUploadId) return;
    try {
      const blob = await api.downloadRightsEvidence({ ...scope, projectId: scope.projectId ?? asset.projectId }, asset, asset.rights.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const ext = { "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" }[blob.type] ?? "bin";
      link.href = url; link.download = asset.name + "." + ext; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setNotice("proofError"); }
  }
  const money = (minor: number) => new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: offer?.currency ?? "CNY" }).format(minor / 100);
  function progressLink(projectId: string, taskId?: string) {
    const query = new URLSearchParams();
    if (scope.episodeId) query.set("episodeId", scope.episodeId);
    if (taskId) query.set("taskId", taskId);
    const path = `/app/workspaces/${encodeURIComponent(scope.workspaceId)}/projects/${encodeURIComponent(projectId)}/generation`;
    return query.size ? path + "?" + query.toString() : path;
  }
  return (
    <dialog ref={dialog} aria-labelledby={title} className="xj-permission-dialog" onCancel={(event) => { event.preventDefault(); close(); }}>
      <header><div className="xj-rights-symbol"><Icon aria-hidden="true" /></div><h2 id={title}>{t(mode)}</h2>
        <button type="button" className="xj-lib-close" aria-label={t("close")} disabled={busy} onClick={close}><X size={20} /></button>
      </header>
      <form onSubmit={(event) => void submit(event)}>
        <div className="xj-permission-body">
          <p>{t(isImage ? "imageBatchHint" : isRegistration ? "registerBatchHint" : "reviewBatchHint", { count: items.length })}</p>
          {assets.length > items.length && <p>{t("batchSkipped", { count: assets.length - items.length })}</p>}
          <ul className="xj-batch-list">{items.map((asset) => (
            <li key={asset.id}><div><strong>{asset.name}</strong>
              <small>{t(asset.type)} · {t(asset.rightsStatus)}{mode === "reviewMany" && asset.rights ? " · " + [asset.rights.holder,
                t(asset.rights.licenseScope === "workspace" ? "workspaceScope" : asset.rights.licenseScope === "project_allowlist" ? "allowlist" : "projectScope"),
                asset.rights.validTo?.slice(0,10) || t("unspecified")].filter(Boolean).join(" · ") : ""}</small></div>
              {rows[asset.id] ? <div><span className={rows[asset.id].failed ? "xj-batch-error" : ""}>{t(rows[asset.id].label)}</span>
                {!busy && scope.projectId && rows[asset.id].taskId && <a className="xj-lib-textbtn" href={progressLink(scope.projectId, rows[asset.id].taskId)} aria-label={t("imageProgress", { name: asset.name })}>{t("seeProgress")}</a>}</div>
                : mode === "reviewMany" && (asset.rights?.proofUploadId
                  ? api.downloadRightsEvidence && <button type="button" className="xj-lib-textbtn" onClick={() => void download(asset)}>{t("download")}</button>
                  : <small>{t("noProof")}</small>)}
            </li>
          ))}</ul>
          {isRegistration && <fieldset disabled={busy || started}>
            <div className="xj-lib-fieldpair">
              <label>{t("holder")}<input required maxLength={256} value={holder} onChange={(e) => setHolder(e.target.value)} placeholder={t("holderHint")} /></label>
              <label>{t("scope")}<select value={licenseScope} onChange={(e) => setLicenseScope(e.target.value)}>
              <option value="owner_project_only">{t("projectScope")}</option><option value="workspace">{t("workspaceScope")}</option></select></label>
            </div>
            <LicenseTermField value={validTo} onChange={setValidTo} />
            <LicenseProofField file={file} onChange={setFile} />
          </fieldset>}
          {!isImage && <label className="xj-attest"><input type="checkbox" checked={attested} disabled={busy || started} onChange={(e) => setAttested(e.target.checked)} />
            {t(isRegistration ? "batchProofCovers" : "attest")}</label>}
          {isImage && !busy && targets.length > 0 && (offer?.available && offer.estimatedMinor != null && offer.currency
            ? <p>{t("batchEstimate", { count: targets.length, amount: money(targets.length * offer.estimatedMinor) })}
                {offer.successfulOutputMinor != null && " " + t("batchUnitPrice", { amount: money(offer.successfulOutputMinor) })}</p>
            : <p>{t(models.notice === "checking" ? "loading" : "batchImageUnavailable")} <button type="button" className="xj-lib-textbtn" onClick={models.recheck}>{t("reload")}</button></p>)}
          {notice && <p role="alert" className="xj-lib-warning">{t(notice)}</p>}
          {started && <p role="status">{t("batchProgress", { done: Object.values(rows).filter((row) => !row.failed && row.label !== "batchSending").length, total: items.length })}</p>}
          {!busy && offer?.canViewBilling && Object.values(rows).some((row) => row.label === "batchCredit") &&
            <a className="xj-lib-textbtn" href={`/app/workspaces/${encodeURIComponent(scope.workspaceId)}/billing`}>{t("balanceLink")}</a>}
          {isImage && started && scope.projectId && <a className="xj-lib-textbtn" href={progressLink(scope.projectId)}
            aria-disabled={busy} onClick={(event) => { if (busy) event.preventDefault(); }}>{t("batchTrack")}</a>}
        </div>
        <footer><button type="button" className="xj-lib-btn" disabled={busy} onClick={close}>{t("done")}</button>
          {(busy || targets.length > 0) && <button type="submit" className="xj-lib-primary" disabled={!maySubmit}>{t(busy ? "batchSending" : started ? "batchRetry" : mode, { count: targets.length })}</button>}
        </footer>
      </form>
    </dialog>
  );
}
