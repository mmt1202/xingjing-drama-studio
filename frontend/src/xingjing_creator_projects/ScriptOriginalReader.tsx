import { useCallback } from "react";
import { BookOpen } from "lucide-react";
import type { Episode, EpisodeSource, EpisodesApi } from "./episodes-api";
import { ReadStatus, ReaderLayout, ReadingText } from "./ScriptReaderLayout";
import { useLibraryTranslation } from "./script-library-i18n";
import { positiveNumber, readerPageSize, useLibraryRead, type LibraryChange } from "./script-library-read";
import { scriptButton } from "./script-workflow";

interface Props {
  projectId: string; source: EpisodeSource; sources: EpisodeSource[]; episode?: Episode;
  api: EpisodesApi; query: URLSearchParams; change: LibraryChange;
  onPlan: (source: EpisodeSource) => void; onDocument: (id: string) => void; canManage: boolean;
}
export function ScriptOriginalReader({ projectId, source, sources, episode, api, query, change, onPlan, onDocument, canManage }: Props) {
  const { t } = useLibraryTranslation();
  const load = useCallback((signal: AbortSignal) => api.chapterDirectory(projectId, source.path, signal), [api, projectId, source.path]);
  const catalog = useLibraryRead(load);
  const data = catalog.data;
  const ref = episode?.workingScript?.sourceRef;
  const boundPath = ref?.sourceId ?? episode?.sourceRange?.source_file;
  const boundStart = ref?.start ?? episode?.sourceRange?.start;
  const unchanged = ref ? ref.digest === data?.source.digest : !episode?.issues.includes("source_changed");
  const boundChapter = source.path === boundPath && unchanged && boundStart !== undefined ?
    data?.chapters.find(chapter => chapter.start <= boundStart && chapter.end > boundStart) : undefined;
  const ordinal = positiveNumber(query.get("chapter"));
  const chapter = ordinal ? data?.chapters.find(item => item.ordinal === ordinal) : boundChapter ?? data?.chapters[0];
  const sectionName = (item: NonNullable<typeof chapter>) => item.title || (item.kind === "preamble" ? t("preamble") : item.kind === "body" ? t("body") : t("chapter", { number: item.ordinal }));
  const bookName = (book: EpisodeSource) => {
    if (book.kind !== "file") return book.label;
    const filename = book.label.split(/[\\/]/).at(-1);
    return filename === "source.txt" ? t("bookFallback") : filename || t("savedFile");
  };
  const choices = <label className="xj-reader-select">{t("source")}<select value={source.path} onChange={e => change({ book: e.target.value, chapter: null, part: null, q: null, listPage: null })}>
    {sources.map((book, i) => <option key={book.path} value={book.path}>{bookName(book)}{sources.some((other, j) => j !== i && bookName(other) === bookName(book)) ? " · " + (i + 1) : ""}</option>)}
  </select></label>;
  const sourceDocumentId = source.scriptId;
  if (!data) return <div className="xj-reader-notice">{choices}<ReadStatus failed={catalog.failed} retry={catalog.retry}/>
    {catalog.failed && sourceDocumentId && <button type="button" className={scriptButton} onClick={() => onDocument(sourceDocumentId)}>{t("backDocument")}</button>}</div>;
  return <ReaderLayout kind="chapters" items={data.chapters.map(item => ({ number: item.ordinal, title: sectionName(item), status: item === boundChapter ? t("bound") : undefined }))}
    selected={chapter?.ordinal} query={query} change={change} beforeList={choices}
    select={(number, page) => change({ chapter: number, listPage: page, part: null })}>
    {chapter ? <><div className="xj-reader-heading"><div><p><BookOpen size={15}/>{bookName(source)}</p><h2>{sectionName(chapter)}</h2>
      <small>{t("characters", { count: (chapter.end - chapter.start).toLocaleString() })}{chapter === boundChapter && " · " + t("bound")}</small></div></div>
      <div className="xj-reader-actions">
        {boundPath && sources.some(item => item.path === boundPath) && (source.path !== boundPath || (boundChapter && chapter !== boundChapter)) &&
          <button className={scriptButton} type="button" onClick={() => change({ book: boundPath, chapter: null, listPage: null, q: null, part: null })}>{t("goToSource")}</button>}
        {canManage && source.scriptId && <button className={scriptButton} type="button" onClick={() => onPlan(source)}>{t("split")}</button>}
        {sourceDocumentId && <button className={scriptButton} type="button" onClick={() => onDocument(sourceDocumentId)}>{t("backDocument")}</button>}
      </div>
      {source.path === boundPath && !unchanged && <p className="xj-reader-note">{t("sourceChanged")}</p>}
      <OriginalExcerpt api={api} projectId={projectId} source={data.source} start={chapter.start} end={chapter.end} query={query} change={change} reloadCatalog={catalog.retry}/>
    </> : <p className="xj-reader-notice">{t(data.chapters.length ? "missing" : "emptyText")}</p>}
  </ReaderLayout>;
}

function OriginalExcerpt({ api, projectId, source, start, end, query, change, reloadCatalog }: {
  api: EpisodesApi; projectId: string; source: EpisodeSource; start: number; end: number; query: URLSearchParams; change: LibraryChange; reloadCatalog: () => void;
}) {
  const pages = Math.max(1, Math.ceil((end - start) / readerPageSize));
  const page = Math.min(positiveNumber(query.get("part")) ?? 1, pages);
  const from = start + (page - 1) * readerPageSize, to = Math.min(end, from + readerPageSize);
  const read = useCallback(async (signal: AbortSignal) => {
    if (from === to) return "";
    const result = await api.sourceExcerpt(projectId, source, from, to, signal);
    if (result.digest !== source.digest || result.path !== source.path || result.start !== from || result.end !== to) throw new Error("SOURCE_EXCERPT_MISMATCH");
    return result.text;
  }, [api, projectId, source, from, to]);
  const excerpt = useLibraryRead(read);
  return excerpt.data === undefined ? <ReadStatus failed={excerpt.failed} retry={reloadCatalog}/> :
    <ReadingText key={from} text={excerpt.data} page={page} pages={pages} select={value => change({ part: value })}/>;
}
