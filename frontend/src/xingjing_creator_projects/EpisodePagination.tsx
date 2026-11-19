import { useMemo, useState } from "react";
import { useEpisodesTranslation } from "./episodes-i18n";

export const EPISODE_PAGE_SIZE = 20;

export function useEpisodeListPage<T>(items: readonly T[], pageSize: number, storageKey: string, initialPage = 1) {
  const remembered = useMemo(() => {
    try {
      const page = Number(sessionStorage.getItem(storageKey));
      if (Number.isSafeInteger(page) && page > 0) return page;
    } catch { /* The list can also be paged in memory. */ }
    return undefined;
  }, [storageKey]);
  const [position, setPosition] = useState<{ key: string; page: number }>();
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(position?.key === storageKey ? position.page : remembered ?? initialPage, pageCount);
  const offset = (page - 1) * pageSize;
  function setPage(next: number) {
    setPosition({ key: storageKey, page: next });
    try { sessionStorage.setItem(storageKey, String(next)); } catch { /* Keep the in-memory position. */ }
  }
  return { page, pageCount, total, first: offset + 1, last: Math.min(offset + pageSize, total), items: items.slice(offset, offset + pageSize), setPage };
}

type PageInfo = Pick<ReturnType<typeof useEpisodeListPage>, "page" | "pageCount" | "total" | "first" | "last" | "setPage">;
type PageLabel = "chapterPages" | "candidatePages" | "draftPages" | "episodePages";
const control = "min-h-9 rounded-lg border border-white/15 bg-[var(--xj-bg-elevated,#0c1018)] px-3 py-1 text-sm text-slate-200 hover:border-violet-300/50 focus-visible:outline-2 focus-visible:outline-violet-300 disabled:cursor-not-allowed disabled:opacity-40";

export function EpisodePagination({ list, label }: { list: PageInfo; label: PageLabel }) {
  const { t } = useEpisodesTranslation();
  if (list.pageCount === 1) return null;
  return <nav aria-label={t(label)} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3">
    <span role="status" className="text-xs tabular-nums text-slate-400">
      {t("pageRange", { first: list.first, last: list.last, count: list.total })}
    </span>
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={control} disabled={list.page === 1} onClick={() => list.setPage(list.page - 1)}>{t("previous")}</button>
      <select className={control} aria-label={t("jumpToPage")} value={list.page} onChange={event => list.setPage(Number(event.target.value))}>
        {Array.from({ length: list.pageCount }, (_, index) => <option key={index} value={index + 1}>{t("page", { current: index + 1, total: list.pageCount })}</option>)}
      </select>
      <button type="button" className={control} disabled={list.page === list.pageCount} onClick={() => list.setPage(list.page + 1)}>{t("next")}</button>
    </div>
  </nav>;
}
