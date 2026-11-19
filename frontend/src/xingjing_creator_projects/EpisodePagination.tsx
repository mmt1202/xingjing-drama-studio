import { useMemo, useState, type RefObject } from "react";
import { Pagination } from "@/components/ui/Pagination";
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
  return { page, pageCount, pageSize, offset, total, items: items.slice(offset, offset + pageSize), setPage };
}

type PageInfo = Pick<ReturnType<typeof useEpisodeListPage>, "offset" | "pageSize" | "total" | "setPage">;
type PageLabel = "chapterPages" | "candidatePages" | "draftPages" | "episodePages";
export function EpisodePagination({ list, label, listStart }: { list: PageInfo; label: PageLabel; listStart?: RefObject<HTMLElement | null> }) {
  const { t } = useEpisodesTranslation();
  return <Pagination offset={list.offset} pageSize={list.pageSize} total={list.total} label={t(label)}
    unit={label === "chapterPages" ? "chapters" : "episodes"} onOffsetChange={offset => {
      list.setPage(offset / list.pageSize + 1);
      const start = listStart?.current;
      if (start && start.getBoundingClientRect().top < 0) start.scrollIntoView({ block: "start", behavior: "instant" });
    }} />;
}
