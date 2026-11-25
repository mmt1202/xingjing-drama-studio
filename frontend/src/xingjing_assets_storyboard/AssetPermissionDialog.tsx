import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Download, FileCheck2, ShieldCheck, X } from "lucide-react";
import { useAssetLibraryTranslation } from "./asset-library-i18n";
import { AssetsStoryboardApiError } from "./api";
import type {
  AssetItem,
  AssetsStoryboardPort,
  RightsEvidenceUpload,
  WorkspaceProjectScope,
} from "./contracts";
import { useDraftProtection } from "./useDraftProtection";
import { earliestLicenseEnd, LicenseProofField, LicenseTermField, type LicenseEnd } from "./LicenseFields";

const scopeLabels = {
  owner_project_only: "projectScope",
  workspace: "workspaceScope",
  project_allowlist: "allowlist",
} as const;
export interface PermissionDraft {
  holder: string;
  licenseScope: string;
  proofObjectKey: string;
  proofSha256: string;
  validTo: string;
}
function dateField(value?: string) {
  return value?.slice(0, 10) ?? "";
}
function registrationEnd(asset: AssetItem, savedEnd?: string): LicenseEnd {
  const end = dateField(savedEnd ?? asset.rights?.validTo);
  if (end) return end >= earliestLicenseEnd() ? end : "";
  return asset.rightsStatus === "expired" ? "" : null;
}

export function AssetPermissionDialog({
  initial,
  seed,
  editable,
  api,
  scope,
  onClose,
  onSaved,
}: {
  initial: AssetItem;
  seed?: PermissionDraft;
  editable: boolean;
  api: AssetsStoryboardPort;
  scope: WorkspaceProjectScope;
  onClose: () => void;
  onSaved: (asset: AssetItem) => void;
}) {
  const { t } = useAssetLibraryTranslation();
  const title = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const inflight = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const [asset, setAsset] = useState(initial);
  const [holder, setHolder] = useState(seed?.holder ?? initial.rights?.holder ?? "");
  const initialScope = seed?.licenseScope ?? initial.rights?.licenseScope;
  const [licenseScope, setLicenseScope] = useState(
    initialScope ?? "owner_project_only",
  );
  const [validTo, setValidTo] = useState<LicenseEnd>(() => registrationEnd(initial, seed?.validTo));
  const [file, setFile] = useState<File>();
  const [upload, setUpload] = useState<Pick<RightsEvidenceUpload, "sha256" | "objectKey"> | undefined>(
    seed?.proofSha256 && seed.proofObjectKey
      ? { sha256: seed.proofSha256, objectKey: seed.proofObjectKey }
      : undefined,
  );
  const [renew, setRenew] = useState(Boolean(seed));
  const [touched, setTouched] = useState(false);
  const [attest, setAttest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [notice, setNotice] = useState<"saved" | "confirmed">();
  const [error, setError] = useState<
    "dateError" | "failed" | "conflict" | "proofError" | "ownerOnly" | "holderNeeded"
  >();
  const form = editable && (renew || ["missing", "expired", "blocked"].includes(asset.rightsStatus));
  useDraftProtection(busy || touched);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      controller.current?.abort();
      dialog?.close();
    };
  }, []);
  function close() {
    if (inflight.current) return;
    if (touched) setDiscard(true);
    else onClose();
  }
  function selectFile(next?: File) {
    setTouched(true);
    setError(undefined);
    setUpload(undefined);
    setFile(next);
  }
  async function perform(
    operation: (signal: AbortSignal) => Promise<void>,
    fallback: "failed" | "proofError" = "failed",
  ) {
    if (inflight.current) return;
    inflight.current = true;
    const operationController = new AbortController();
    controller.current = operationController;
    setBusy(true);
    setError(undefined);
    try {
      await operation(operationController.signal);
    } catch (cause) {
      if (operationController.signal.aborted) return;
      const apiError = cause instanceof AssetsStoryboardApiError ? cause : undefined;
      setError(
        apiError?.status === 403
          ? "ownerOnly"
          : apiError?.code === "RIGHTS_EVIDENCE_NOT_VERIFIED"
            ? "proofError"
            : apiError?.code === "RIGHTS_EVIDENCE_EXPIRED" || apiError?.code === "RIGHTS_EXPIRY_INVALID"
              ? "dateError"
              : apiError?.status === 409
                ? "conflict"
                : fallback,
      );
    } finally {
      inflight.current = false;
      if (!operationController.signal.aborted) setBusy(false);
    }
  }
  function accept(saved: AssetItem, status: "saved" | "confirmed") {
    setAsset(saved);
    setTouched(false);
    setRenew(false);
    setAttest(false);
    setFile(undefined);
    setUpload(undefined);
    setNotice(status);
    onSaved(saved);
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!editable || !form) return;
    if (!holder.trim()) {
      setError("holderNeeded");
      return;
    }
    if (validTo !== null && validTo < earliestLicenseEnd()) {
      setError("dateError");
      return;
    }
    void perform(async (signal) => {
      const proof = upload ?? (file && (await api.uploadRightsEvidence(scope, asset, file, signal)));
      if (signal.aborted) return;
      setUpload(proof);
      const result = await api.assetAction(
        scope,
        {
          action: "saveRights",
          targetId: asset.id,
          version: asset.version,
          payload: {
            ownerProjectId: asset.projectId,
            holder: holder.trim(),
            licenseScope,
            ...(licenseScope === "project_allowlist" ? { allowedProjectIds: asset.rights?.allowedProjectIds } : {}),
            validTo: validTo || undefined,
            ...(proof ? { proofObjectKey: proof.objectKey, proofSha256: proof.sha256 } : {}),
          },
        },
        signal,
      );
      if (!result.asset) throw new Error();
      if (!signal.aborted) accept(result.asset, "saved");
    });
  }
  function confirm() {
    if (!editable || !attest || asset.rightsStatus !== "pending" || !asset.rights?.id) return;
    const rightsRecordId = asset.rights.id;
    void perform(async (signal) => {
      const result = await api.assetAction(
        scope,
        {
          action: "confirmRights",
          targetId: asset.id,
          version: asset.version,
          payload: { rightsRecordId, ownerProjectId: asset.projectId },
        },
        signal,
      );
      if (!result.asset) throw new Error();
      if (!signal.aborted) accept(result.asset, "confirmed");
    });
  }
  function download() {
    if (!asset.rights?.id || !asset.rights.proofUploadId || !api.downloadRightsEvidence) return;
    const id = asset.rights.id;
    void perform(async (signal) => {
      const data = await api.downloadRightsEvidence?.(scope, asset, id, signal);
      if (!data) throw new Error();
      if (signal.aborted) return;
      const extension =
        { "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" }[
          data.type
        ] ?? "bin";
      const url = URL.createObjectURL(data),
        link = document.createElement("a");
      link.href = url;
      link.download = asset.name + "-" + t("proof") + "." + extension;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "proofError");
  }
  function reload() {
    void perform(async (signal) => {
      if (!api.getAsset) throw new Error();
      const fresh = await api.getAsset(scope, asset.id, signal);
      if (signal.aborted) return;
      setAsset(fresh);
      setAttest(false);
      setNotice(undefined);
      // Keep an unsubmitted form visible so the member can compare and resubmit.
      setRenew(touched);
      onSaved(fresh);
    });
  }
  return (
    <dialog
      ref={ref}
      aria-labelledby={title}
      className="xj-permission-dialog"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <header>
        <div className="xj-rights-symbol">
          <ShieldCheck aria-hidden="true" />
        </div>
        <div>
          <p>
            {t("status")} · {t(asset.type)}
          </p>
          <h2 id={title}>{asset.name}</h2>
        </div>
        <button
          type="button"
          className="xj-lib-close"
          aria-label={t("close")}
          disabled={busy}
          onClick={close}
        >
          <X size={20} />
        </button>
      </header>
      <div className="xj-permission-body">
        <span className={"xj-lib-state is-" + asset.rightsStatus}>{t(asset.rightsStatus)}</span>
        <p className="xj-permission-help">{t((asset.rightsStatus + "Help") as "missingHelp")}</p>
        {notice ? (
          <div role="status" className="xj-lib-success">
            <FileCheck2 size={18} />
            {t(notice)}
          </div>
        ) : null}
        {asset.rights && (
          <details className="xj-permission-record" open={!form} key={form ? "editing" : "viewing"}>
            <summary>
              {t(form ? "previousRegistration" : "record")}
              {form && <span>{asset.rights.validTo
                ? t("expirySummary", { date: dateField(asset.rights.validTo) })
                : t("unspecified")}</span>}
            </summary>
            <dl>
              <div>
                <dt>{t("holder")}</dt>
                <dd>{asset.rights.holder || t("unknown")}</dd>
              </div>
              <div>
                <dt>{t("scope")}</dt>
                <dd>{t(scopeLabels[asset.rights.licenseScope as keyof typeof scopeLabels] ?? "unknown")}</dd>
              </div>
              <div>
                <dt>{t("until")}</dt>
                <dd>{dateField(asset.rights.validTo) || t("unspecified")}</dd>
              </div>
            </dl>
            {editable && asset.rights.id && asset.rights.proofUploadId && api.downloadRightsEvidence ? (
              <button type="button" className="xj-lib-textbtn" disabled={busy} onClick={download}>
                <Download size={15} />
                {t("download")}
              </button>
            ) : !asset.rights.proofUploadId ? <small>{t("noProof")}</small> : null}
          </details>
        )}
        {error ? (
          <div role="alert" className="xj-lib-warning">
            {t(error)}{" "}
            {error === "conflict" ? (
              <button type="button" className="xj-lib-textbtn" onClick={reload} disabled={busy}>
                {t("reload")}
              </button>
            ) : null}
          </div>
        ) : null}
        {form ? (
          <form onSubmit={submit}>
            <fieldset
              disabled={busy}
              onChange={() => {
                setTouched(true);
                setNotice(undefined);
              }}
            >
              <div className="xj-lib-fieldpair">
                <label>
                  {t("holder")}
                  <input
                    required
                    maxLength={256}
                    value={holder}
                    placeholder={t("holderHint")}
                    onChange={(event) => setHolder(event.target.value)}
                  />
                </label>
                <label>
                  {t("scope")}
                  <select
                    aria-label={t("scope")}
                    value={licenseScope}
                    onChange={(event) => setLicenseScope(event.target.value)}
                  >
                    <option value="owner_project_only">{t("projectScope")}</option>
                    <option value="workspace">{t("workspaceScope")}</option>
                    {asset.rights?.licenseScope === "project_allowlist" && <option value="project_allowlist">{t("allowlist")}</option>}
                  </select>
                </label>
              </div>
              <LicenseTermField value={validTo} onChange={(end) => {
                setValidTo(end); setTouched(true); setNotice(undefined);
                if (error === "dateError") setError(undefined);
              }} />
              <LicenseProofField file={file} uploaded={Boolean(upload)} onChange={selectFile} />
              <button type="submit" className="xj-lib-primary" disabled={busy || error === "conflict"}>
                {t(busy ? "sending" : "send")}
              </button>
            </fieldset>
          </form>
        ) : editable ? (
          <div className="xj-permission-actions">
            {asset.rightsStatus === "pending" ? (
              <>
                <label className="xj-attest">
                  <input
                    type="checkbox"
                    checked={attest}
                    disabled={busy}
                    onChange={(event) => setAttest(event.target.checked)}
                  />
                  {t("attest")}
                </label>
                <button
                  type="button"
                  className="xj-lib-primary"
                  disabled={!attest || busy || !asset.rights?.id || error === "conflict"}
                  onClick={confirm}
                >
                  {t("confirm")}
                </button>
              </>
            ) : null}
            <button
              type="button"
              className="xj-lib-btn"
              disabled={busy}
              onClick={() => {
                setHolder(asset.rights?.holder ?? "");
                setLicenseScope(asset.rights?.licenseScope ?? "owner_project_only");
                setValidTo(registrationEnd(asset));
                setRenew(true);
                setNotice(undefined);
                setError(undefined);
              }}
            >
              {t("replace")}
            </button>
          </div>
        ) : (
          <p className="xj-lib-note">{t("ownerOnly")}</p>
        )}
      </div>
      <footer>
        {discard ? (
          <div className="xj-discard">
            <strong>{t("discard")}</strong>
            <button type="button" className="xj-lib-btn" onClick={() => setDiscard(false)}>
              {t("keep")}
            </button>
            <button type="button" className="xj-lib-textbtn" onClick={onClose}>
              {t("discardYes")}
            </button>
          </div>
        ) : (
          <button type="button" className="xj-lib-btn" disabled={busy} onClick={close}>
            {t("done")}
          </button>
        )}
      </footer>
    </dialog>
  );
}
