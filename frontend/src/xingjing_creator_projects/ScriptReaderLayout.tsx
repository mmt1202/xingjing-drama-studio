import { useId, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, List, Search } from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { useLibraryTranslation } from "./script-library-i18n";
import { positiveNumber, type LibraryChange } from "./script-library-read";
import { useScriptTranslation } from "./script-i18n";
import { scriptButton } from "./script-workflow";

export function ReadStatus({ failed, retry, directory }: { failed: boolean; retry: () => void; directory?: boolean }) {
  const { t } = useLibraryTranslation(), { t: script } = useScriptTranslation();
  return <div className="xj-reader-notice" role="status">{failed ? <>{t(directory ? "indexError" : "readError")}
    <button type="button" className={scriptButton} onClick={retry}>{t("reload")}</button></> : script("loading")}</div>;
}

export interface DirectoryItem { number: number; title: string; label?: string; status?: string }
export function ReaderLayout({ kind, items, selected, query, change, select, children, beforeList }: {
  kind: "episodes" | "chapters"; items: DirectoryItem[]; selected?: number; query: URLSearchParams; change: LibraryChange;
  select: (number: number, page: number) => void; beforeList?: ReactNode; children: ReactNode;
}) {
  const { t } = useLibraryTranslation();
  const id = useId(), [expanded, setExpanded] = useState(false);
  const episodes = kind === "episodes", search = query.get("q") ?? "";
  const match = search.trim().toLocaleLowerCase();
  const filtered = match ? items.filter(item => (item.number + " " + (item.label ?? "") + " " + item.title).toLocaleLowerCase().includes(match)) : items;
  const index = filtered.findIndex(item => item.number === selected);
  const size = 8;
  const last = Math.max(1, Math.ceil(filtered.length / size));
  const page = Math.min(last, positiveNumber(query.get("listPage")) ?? Math.floor(Math.max(index, 0) / size) + 1);
  const selectAt = (position: number) => {
    if (!filtered[position]) return;
    select(filtered[position].number, Math.floor(position / size) + 1);
    setExpanded(false);
  };
  return <section className="xj-reading-layout">
    <aside className="xj-reading-directory">
      <header><h2>{t(episodes ? "episodeDirectory" : "chapterDirectory")}</h2><small>{t(episodes ? "episodeCount" : "chapterCount", { count: items.length })}</small>
        <button type="button" className="xj-directory-toggle" aria-controls={id} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><List size={16}/>{t(expanded ? "collapseList" : "chapterList")}</button>
      </header>
      <div id={id} className="xj-directory-content" data-expanded={expanded}>
        {beforeList}
        <label className="xj-reader-search"><Search size={15} aria-hidden="true"/><input type="search" maxLength={200} aria-label={t(episodes ? "searchEpisodes" : "searchChapters")} placeholder={t(episodes ? "searchEpisodes" : "searchChapters")} value={search} onChange={e => change({ q: e.target.value || null, listPage: 1 }, true)}/></label>
        <ul>{filtered.slice((page - 1) * size, page * size).map((item, pos) => <li key={item.number}>
          <button type="button" aria-current={item.number === selected ? "true" : undefined} onClick={() => selectAt((page - 1) * size + pos)}>
            {(item.label || item.status) && <span className="xj-directory-meta">{item.label}<small>{item.status}</small></span>}
            <span>{item.title}</span>
          </button></li>)}</ul>
        {!filtered.length && search && <div role="status">{t("noMatches")} <button className="underline" type="button" onClick={() => change({ q: null, listPage: null })}>{t("clear")}</button></div>}
        <Pagination offset={(page - 1) * size} pageSize={size} total={filtered.length} unit={kind} label={t(episodes ? "episodeDirectory" : "chapterDirectory")} onOffsetChange={offset => change({ listPage: offset / size + 1 })}/>
      </div>
    </aside>
    <div className="xj-reader-paper">{children}
      {filtered.length > 1 && index >= 0 && <footer className="xj-reader-turn"><button type="button" disabled={index === 0} onClick={() => selectAt(index - 1)}><ChevronLeft size={15}/>{t(episodes ? "previousEpisode" : "previousChapter")}</button>
        <button type="button" disabled={index + 1 >= filtered.length} onClick={() => selectAt(index + 1)}>{t(episodes ? "nextEpisode" : "nextChapter")}<ChevronRight size={15}/></button></footer>}
    </div>
  </section>;
}

export function ReadingText({ text, page, pages, select }: { text: string; page: number; pages: number; select: (page: number) => void }) {
  const { t } = useLibraryTranslation();
  return <><div className="xj-reading-text" role="region" aria-label={t("reader")}>{text || t("emptyText")}</div>
    {pages > 1 && <div className="xj-reader-text-pages"><button type="button" disabled={page <= 1} onClick={() => select(page - 1)}>{t("previousText")}</button>
      <span>{t("part", { page, pages })}</span><button type="button" disabled={page >= pages} onClick={() => select(page + 1)}>{t("nextText")}</button></div>}</>;
}
