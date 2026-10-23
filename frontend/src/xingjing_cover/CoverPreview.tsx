import { useEffect, useState } from "react";
import { ImageOff, Image as ImageIcon, LoaderCircle, RefreshCw } from "lucide-react";
import type { CoverApi, CoverCandidate, CoverPlan } from "./contracts";
import { CoverApiError } from "./api";
import { coverButton, useCoverTranslation } from "./i18n";
import "./cover.css";

interface PreviewState { api: CoverApi; key: string; url?: string; error?: unknown }

export function CoverPreview({ api, plan, candidate, title, overlay = true }: {
  api: CoverApi; plan: CoverPlan; candidate?: CoverCandidate; title?: string; overlay?: boolean;
}) {
  const { t } = useCoverTranslation();
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<PreviewState>();
  const assetId = candidate?.assetId;
  const available = candidate?.available === true;
  const key = `${plan.id}:${assetId ?? ""}:${reload}`;
  useEffect(() => {
    if (!assetId || !available) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void api.media(plan.id, assetId, controller.signal).then((blob) => {
      if (controller.signal.aborted) return;
      if (!blob.size || !blob.type.startsWith("image/")) throw new Error("Invalid image response");
      objectUrl = URL.createObjectURL(blob);
      setState({ api, key, url: objectUrl });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ api, key, error });
    });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, plan.id, assetId, available, key]);
  const current = state?.api === api && state.key === key ? state : undefined;
  const error = current?.error;
  const failed = error !== undefined;
  const url = current?.url;
  const ratio = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(plan.aspectRatio);
  const aspectRatio = ratio && Number(ratio[1]) > 0 && Number(ratio[2]) > 0 ? `${ratio[1]} / ${ratio[2]}` : "9 / 16";
  const numericRatio = ratio && Number(ratio[1]) > 0 && Number(ratio[2]) > 0 ? Number(ratio[1]) / Number(ratio[2]) : 9 / 16;
  const { x, y, width, height } = plan.titleSafeArea;
  return <figure className="xj-cover-preview">
    <div className="xj-cover-preview-frame" style={{ aspectRatio, maxWidth: `${360 * numericRatio}px`, marginInline: "auto" }} aria-busy={available && !current}>
      {url && !failed && available ? <img className="xj-cover-preview-image" src={url} alt={t("previewAlt", { name: plan.name })} onError={() => setState({ api, key, error: new Error("Image decoding failed") })} /> : null}
      {!candidate ? <div className="xj-cover-preview-status"><ImageIcon size={32} aria-hidden="true" /><span>{t("emptyCandidates")}</span></div>
        : !available || failed ? <div className="xj-cover-preview-status" role="status"><ImageOff size={32} aria-hidden="true" /><span>{t(error instanceof CoverApiError && error.status === 403 ? "previewPermission" : "mediaUnavailable")}</span>{available ? <button type="button" className={coverButton} onClick={() => setReload((previous) => previous + 1)}><RefreshCw size={14} aria-hidden="true" />{t("retry")}</button> : null}</div>
          : !url ? <div className="xj-cover-preview-status" role="status"><LoaderCircle size={28} aria-hidden="true" className="animate-spin" /><span>{t("previewLoading")}</span></div> : null}
      {url && !failed && available && overlay ? <div className="xj-cover-safe-area" style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}>
        <span className="xj-cover-safe-area-label">{t("safeAreaLabel")}</span>{title ? <span className="xj-cover-preview-title">{title}</span> : null}
      </div> : null}
    </div>
  </figure>;
}
