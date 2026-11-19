import { useEffect, useState } from "react";
import { AudioLines, Box, ChevronRight, FolderOpen, MapPin, Shirt, UserRound } from "lucide-react";
import { useSearchParams } from "wouter";
import { Pagination } from "@/components/ui/Pagination";
import { useAssetExtractionTranslation } from "./asset-extraction-i18n";
import type { AssetType, AssetsStoryboardPort, BatchItemResult, BatchReceipt, ExtractionHistory, WorkspaceProjectScope } from "./contracts";
import { button, panel } from "./ui";

export const extractionTypes = ["character", "scene", "prop", "costume", "voice"] as const;
const icons = { character: UserRound, scene: MapPin, prop: Box, costume: Shirt, voice: AudioLines };
const pageSize = 8;

export function useExtractionLocation() {
  const [query, setQuery] = useSearchParams();
  const type = extractionTypes.find((kind) => kind === query.get("extractionType"));
  const page = Number(query.get("extractionPage"));
  function select(operationId?: string, kind?: AssetType, pageNo = 1, version?: string) {
    setQuery((current) => {
      const next = new URLSearchParams(current);
      if (operationId) next.set("extractionRun", operationId); else next.delete("extractionRun");
      if (kind) next.set("extractionType", kind); else next.delete("extractionType");
      if (pageNo > 1) next.set("extractionPage", String(pageNo)); else next.delete("extractionPage");
      if (version !== undefined) {
        if (version) next.set("scriptVersionId", version); else next.delete("scriptVersionId");
      }
      return next;
    }, { replace: true });
  }
  return { run: query.get("extractionRun") || undefined, type, page: Number.isSafeInteger(page) && page > 0 ? page : 1, select };
}

function ResultRow({ item, busy, onOpen }: { item: BatchItemResult; busy: boolean; onOpen: (id: string) => void }) {
  const { t } = useAssetExtractionTranslation();
  const kind = extractionTypes.find((value) => value === item.type);
  const Icon = kind ? icons[kind] : FolderOpen;
  const saved = item.status === "succeeded";
  const name = item.name?.trim() || t("unnamed");
  // Provider error codes belong in diagnostics. Only show an actual description or a human-readable reason here.
  const detail = item.description?.trim() || (!saved && item.message !== item.code ? item.message : undefined);
  return <li>
    <div className="group relative flex items-center gap-3 px-4 py-3.5 sm:gap-4 sm:px-5">
      <span className="flex min-w-[4.75rem] shrink-0 items-center gap-1.5 self-start pt-0.5 text-xs font-medium text-violet-200">
        <Icon size={15} aria-hidden="true" /><span>{t(kind ?? "uncategorized")}</span>
      </span>
      <div className="min-w-0 grow">
        {saved ? <button type="button" disabled={busy} onClick={() => onOpen(item.id)} aria-label={t("openAsset", { name })}
          className="break-words text-left text-sm font-semibold text-[#F5F6FA] after:absolute after:inset-0 hover:text-violet-200 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-inset focus-visible:after:ring-violet-400">
          {name}</button> : <span className="break-words text-sm font-semibold">{name}</span>}
        {detail ? <p className="mt-1 line-clamp-2 break-words text-xs leading-5 text-[#AEB4C3]">{detail}</p> : null}
      </div>
      {saved ? <ChevronRight size={16} aria-hidden="true" className="shrink-0 text-[#9299AA] group-hover:text-violet-200" /> :
        <span className="shrink-0 text-xs text-amber-200">{t(item.status)}</span>}
    </div>
  </li>;
}

export function ExtractionResults({ api, scope: { workspaceId, projectId }, version, live, location, busy, onOpen }: {
  api: AssetsStoryboardPort; scope: WorkspaceProjectScope; version: string; live?: BatchReceipt;
  location: ReturnType<typeof useExtractionLocation>; busy: boolean; onOpen: (id: string) => void;
}) {
  const { t, i18n } = useAssetExtractionTranslation();
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; data?: ExtractionHistory; failed?: boolean }>();
  const { run, type, page, select } = location;
  const key = JSON.stringify([workspaceId, projectId, version, run, retry]);
  useEffect(() => {
    if (!api.getExtractionHistory) return;
    const controller = new AbortController();
    void api.getExtractionHistory({ workspaceId, projectId }, version, run, controller.signal).then(
      (data) => { if (!controller.signal.aborted) setLoaded({ key, data }); },
      () => { if (!controller.signal.aborted) setLoaded({ key, failed: true }); },
    );
    return () => controller.abort();
  }, [api, workspaceId, projectId, version, run, key]);

  const current = loaded?.key === key ? loaded : undefined;
  const direct = live && live.operationId === run ? live : undefined;
  const batch = direct ?? current?.data?.batch;
  const runs = current?.failed ? [] : (loaded?.data?.runs ?? []);
  const items = batch?.items ?? [];
  const filtered = type ? items.filter((item) => item.type === type) : items;
  const offset = (Math.min(page, Math.max(1, Math.ceil(filtered.length / pageSize))) - 1) * pageSize;
  const notSaved = items.filter((item) => item.status !== "succeeded").length;
  const timestamp = new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short" });
  if (!api.getExtractionHistory && !batch) return null;
  return <section aria-label={t("results")} className={`${panel} mt-5 max-w-4xl overflow-hidden`}>
    <div className="flex flex-wrap items-start justify-between gap-3 px-4 pt-4 sm:px-5 sm:pt-5">
      <div>
        <h2 className="text-lg font-semibold text-[#F5F6FA]">{t("results")}</h2>
        {batch ? <p role="status" className="mt-1.5 text-sm font-medium text-emerald-200">{t("saved", { count: batch.succeeded })}
          {notSaved > 0 ? <span className="ml-2 text-amber-200">{t("notSaved", { count: notSaved })}</span> : null}</p> : null}
      </div>
      {runs.length > 0 ? <label className="flex min-w-0 max-w-full flex-col gap-1 text-xs text-[#AEB4C3]">
        {t("runs")}
        <select value={run ?? batch?.operationId ?? runs[0].operationId} disabled={busy} onChange={(event) => select(event.target.value)}
          className="max-w-full rounded-lg border border-[#393E4B] bg-[#171922] px-2.5 py-2 text-sm text-[#E2E5EC]">
          {run && !runs.some((entry) => entry.operationId === run) ? <option value={run}>{direct ? t("recentRun", { count: direct.succeeded }) : t("missingRun")}</option> : null}
          {runs.map((entry) => <option key={entry.operationId} value={entry.operationId}>{t("runOption", { time: timestamp.format(new Date(entry.savedAt)), count: entry.count })}</option>)}
        </select>
      </label> : null}
    </div>
    {current?.failed ? <div role="status" className="flex flex-wrap items-center gap-3 px-5 py-4 text-sm text-amber-200">
      {t("historyError")}<button type="button" className={button} onClick={() => setRetry((value) => value + 1)}>{t("reloadRuns")}</button>
    </div> : null}
    {!batch && !current?.failed ? <div role="status" className="px-5 pb-5 pt-3 text-sm text-[#B8BECC]">
      {t(!current ? "loadingRuns" : run ? "missingRun" : "noRuns")}
      {current && run ? <button type="button" className={`${button} ml-2`} onClick={() => select()}>{t("latestRun")}</button> : null}
    </div> : null}
    {batch ? <>
      <p className="px-4 pt-2 text-xs leading-5 text-[#AEB4C3] sm:px-5">{t("openHint")}</p>
      <div role="group" aria-label={t("filter")} className="flex flex-wrap gap-1.5 border-b border-[#343845] px-4 py-4 sm:px-5">
        {([undefined, ...extractionTypes] as const).map((kind) => <button key={kind ?? "all"} type="button" disabled={busy} aria-pressed={kind === type}
          onClick={() => select(batch.operationId, kind)} className="inline-flex items-center gap-2 rounded-full border border-[#3A3F4E] px-3 py-1.5 text-xs text-[#C0C6D4] hover:border-violet-400 focus-visible:ring-2 focus-visible:ring-violet-400 aria-pressed:border-violet-400/60 aria-pressed:bg-violet-400/15 aria-pressed:text-violet-100">
          {t(kind ?? "all")}<span className="tabular-nums">{kind ? items.filter((item) => item.type === kind).length : items.length}</span>
        </button>)}
      </div>
      {filtered.length ? <ul className="divide-y divide-[#292D38]">{filtered.slice(offset, offset + pageSize).map((item) =>
        <ResultRow key={item.id} item={item} busy={busy} onOpen={onOpen} />)}</ul> :
        <p role="status" className="px-5 py-5 text-sm text-[#B8BECC]">{t("noKind", { kind: t(type ?? "all") })}</p>}
      <Pagination label={t("resultPages")} offset={offset} pageSize={pageSize} total={filtered.length} disabled={busy}
        onOffsetChange={(start) => select(batch.operationId, type, Math.floor(start / pageSize) + 1)} />
    </> : null}
  </section>;
}
