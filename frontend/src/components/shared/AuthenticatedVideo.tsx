import { useTranslation } from "react-i18next";
import { AuthenticatedMediaError, useAuthenticatedMedia } from "@/hooks/useAuthenticatedMedia";

export function AuthenticatedVideo({ source, workspaceId, projectId, className, muted = false }: {
  source: string; workspaceId?: string; projectId: string; className?: string; muted?: boolean;
}) {
  const { t } = useTranslation("common");
  const media = useAuthenticatedMedia({ source, workspaceId, projectId });
  if (media.error) return <div role="status" className="rounded border border-amber-300/20 bg-amber-300/5 p-3 text-xs leading-6 text-amber-100"><p>{t(media.error instanceof AuthenticatedMediaError && [401, 403].includes(media.error.status) ? "media_access_denied" : "media_unavailable")}</p><button type="button" className="mt-2 rounded border border-white/15 px-3 py-1.5" onClick={media.retry}>{t("retry")}</button></div>;
  if (!media.url) return <p role="status" className="p-3 text-xs text-slate-400">{t("loading")}</p>;
  return <video className={className} controls muted={muted} preload="metadata" src={media.url} onError={media.decodeFailed}><track kind="captions" /></video>;
}
