import { useEffect, useRef, useState } from "react";
import { Check, Images, RefreshCw } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { ReferencePreview } from "./AssetReferenceGeneration";
import { AssetsStoryboardApiError } from "./api";
import type { AssetsStoryboardPort, CharacterArtworkPage } from "./contracts";
import { useTurnaroundTranslation } from "./turnaround-i18n";

const pageSize = 8;

function ArtworkTilePreview(props: { source: string; workspaceId: string; projectId: string; alt: string }) {
  const element = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!element.current || !window.IntersectionObserver) { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "120px" });
    observer.observe(element.current);
    return () => observer.disconnect();
  }, []);
  return <div ref={element} className="xj-turn-tile-image">{visible && <ReferencePreview {...props} compact className="xj-turn-thumb" />}</div>;
}

export function CharacterArtworkLibrary({ api, workspaceId, projectId, assetId, name, currentId, refreshKey, canEdit, disabled, onAdopt, viewLabel }: {
  api: AssetsStoryboardPort; workspaceId: string; projectId: string; assetId: string; name: string; currentId?: string;
  refreshKey: string; canEdit: boolean; disabled: boolean; onAdopt: (id: string) => void; viewLabel: (view: string) => string;
}) {
  const { t, i18n } = useTurnaroundTranslation();
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; page?: CharacterArtworkPage; denied?: boolean }>();
  const key = JSON.stringify([workspaceId, projectId, assetId, offset, refreshKey, reload]);
  useEffect(() => {
    const request = new AbortController();
    async function load() {
      try {
        if (!api.getCharacterArtworks) throw new Error("Character artwork service missing");
        const page = await api.getCharacterArtworks({ workspaceId, projectId }, assetId, offset, pageSize, request.signal);
        if (request.signal.aborted) return;
        if (offset > 0 && offset >= page.total) setOffset(Math.max(0, Math.floor((page.total - 1) / pageSize) * pageSize));
        else setLoaded({ key, page });
      } catch (error) {
        if (!request.signal.aborted) setLoaded({ key, denied: error instanceof AssetsStoryboardApiError && error.status === 403 });
      }
    }
    void load();
    return () => request.abort();
  }, [api, workspaceId, projectId, assetId, offset, key]);
  const result = loaded?.key === key ? loaded : undefined;
  const page = result?.page;
  const dates = new Intl.DateTimeFormat(i18n.resolvedLanguage, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return <section className="xj-turn-panel xj-turn-library" aria-label={t("artLibrary")}>
    <header><h2><Images aria-hidden="true" />{t("artLibrary")}{page && <small>{t("imageCount", { count: page.total })}</small>}</h2>
      <button type="button" className="xj-turn-link" onClick={() => setReload((n) => n + 1)} disabled={!result}><RefreshCw size={14} />{t("refreshArt")}</button></header>
    {!result ? <p role="status">{t("loadingArt")}</p> : !page ? <p role="status">{t(result.denied ? "artDenied" : "artFailure")}</p> : !page.items.length
      ? <p>{t("emptyArt", { name })}</p> : <>
        <p>{t("artHint")}</p>
        <ul className="xj-turn-tiles">{page.items.map((art) => {
          const kind = t(art.capability === "character_turnaround" ? "sheet" : art.capability === "character_expressions" ? "expressions" : "reference");
          const date = dates.format(new Date(art.created_at));
          const source = `/api/v1/generation-assets/${encodeURIComponent(art.id)}/content?projectId=${encodeURIComponent(projectId)}`;
          return <li key={art.id}>
            <ArtworkTilePreview source={source} workspaceId={workspaceId} projectId={projectId} alt={t("artTitle", { name, kind, date })} />
            <div className="xj-turn-tile-info"><strong>{kind}</strong><time dateTime={art.created_at}>{date}</time>
              {(art.views.length > 0 || art.direction) && <details><summary>{t("artNotes")}</summary>
                {art.views.length > 0 && <p>{art.views.map(viewLabel).join(t("listSeparator"))}</p>}{art.direction && <p>{art.direction}</p>}</details>}
              {art.id === currentId ? <span className="xj-turn-used"><Check size={13} />{t("currentArt")}</span> : canEdit &&
                <button type="button" className="xj-turn-link" disabled={disabled} onClick={() => onAdopt(art.id)}>{t("useArt")}</button>}
            </div>
          </li>;
        })}</ul>
        <Pagination label={t("artPages")} pageSize={pageSize} total={page.total} offset={offset} onOffsetChange={setOffset} />
      </>}
  </section>;
}
