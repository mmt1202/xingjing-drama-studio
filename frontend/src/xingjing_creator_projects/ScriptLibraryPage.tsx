import { useCallback } from "react";
import { BookOpen, FilePlus2, Files, ListVideo } from "lucide-react";
import { ImportCatalog } from "./ScriptImportDocuments";
import { ScriptDocumentReader } from "./ScriptDocumentReader";
import { ScriptOriginalReader } from "./ScriptOriginalReader";
import { ReadStatus, ReaderLayout } from "./ScriptReaderLayout";
import type { Episode, EpisodesApi, EpisodesSnapshot } from "./episodes-api";
import { useImportTranslation } from "./import-i18n";
import { scriptReadingParams } from "./production-navigation";
import { useLibraryTranslation } from "./script-library-i18n";
import { positiveNumber, useLibraryRead, type LibraryChange } from "./script-library-read";
import { scriptButton, scriptPrimary } from "./script-workflow";
import { CreatorPage } from "./shared";
import type { CreatorProjectsPort } from "./types";
import "./script-library.css";

interface Props {
  api: CreatorProjectsPort; episodesApi: EpisodesApi; projectId: string; query: URLSearchParams;
  change: LibraryChange; navigate: (step: string, episode?: number | null) => void; manage?: boolean;
}
const episodeDocument = (item: Episode) => item.workingScript?.scriptId || item.production?.scriptVersionId?.match(/^(.+)@[1-9]\d*$/)?.[1];
const newLocation = { q: null, listPage: null, part: null, chapter: null, v: null, book: null };

export function ScriptLibraryPage(props: Props) {
  const { t } = useImportTranslation();
  return <CreatorPage api={props.api} permission="script.view" title={t("title")} className="xj-script-library">{context =>
    <LibraryWorkspace key={props.projectId} {...props} canManage={props.manage ?? context.permissions.includes("script.manage")} />}</CreatorPage>;
}

function LibraryWorkspace({ api, episodesApi, projectId, query, change, navigate, canManage }: Props & { canManage: boolean }) {
  const { t } = useLibraryTranslation();
  const read = useCallback(async (signal: AbortSignal) => {
    const result = await episodesApi.list(projectId, 0, signal);
    const items = [...result.items];
    while (items.length < (result.total ?? 0)) {
      const more = await episodesApi.list(projectId, items.length, signal);
      if (!more.items.length || more.revision !== result.revision) throw new Error("EPISODE_DIRECTORY_CHANGED");
      items.push(...more.items);
    }
    return { ...result, items };
  }, [episodesApi, projectId]);
  const result = useLibraryRead(read);
  const data = result.data;
  const explicitId = query.get("scriptId"), episodeNo = positiveNumber(query.get("episodeId")), view = query.get("library");
  const matching = (docId: string) => data?.items.find(item => episodeDocument(item) === docId);
  const linkedEpisode = explicitId ? matching(explicitId) : undefined;
  const explicitEpisode = data?.items.find(item => item.episode === episodeNo);
  const episode = linkedEpisode ?? (episodeNo ? explicitEpisode : !explicitId ? data?.items[0] : undefined);
  const detached = Boolean(explicitId && !linkedEpisode);
  const docId = explicitId ?? (episode ? episodeDocument(episode) : undefined);
  const sources = data?.sources ?? [];
  const scopedEpisode = linkedEpisode ?? explicitEpisode;
  const reference = scopedEpisode?.workingScript?.sourceRef.sourceId ?? scopedEpisode?.sourceRange?.source_file;
  const linkedSource = sources.find(item => item.path === reference);
  const importedSource = sources.find(item => item.scriptId && item.scriptId === explicitId);
  const firstEpisode = linkedEpisode ?? explicitEpisode ?? data?.items[0];
  const specificBook = query.get("book");
  const firstSourcedEpisode = data?.items.find(item => item.workingScript || item.sourceRange);
  const projectSource = sources.find(item => item.path === (firstSourcedEpisode?.workingScript?.sourceRef.sourceId ?? firstSourcedEpisode?.sourceRange?.source_file));
  const book = specificBook ? sources.find(item => item.path === specificBook) :
    importedSource ?? linkedSource ?? projectSource ?? sources.find(item => item.kind === "sql_original") ?? sources[0];
  const showOriginal = view === "original" || (!view && !episodeNo && !explicitId && sources.length > 0);
  const selectEpisode = (number: number, page?: number) => {
    const item = data?.items.find(entry => entry.episode === number);
    change({ library: "episodes", episodeId: number, scriptId: item ? episodeDocument(item) ?? null : null, v: null, part: null,
      chapter: null, book: null, listPage: page ?? null, ...(page === undefined ? { q: null } : {}) });
  };
  const selectDocument = (id: string) => {
    const owner = matching(id);
    change({ ...newLocation, library: owner ? "episodes" : "document", episodeId: owner?.episode ?? null, scriptId: id });
  };
  const toOriginal = () => change({ ...newLocation, library: "original", book: (importedSource ?? linkedSource)?.path ?? null });
  const toImport = () => change({ library: "import", backView: view ?? (showOriginal ? "original" : "episodes") });
  const open = (step: string, id: string, number?: number) => {
    const params = new URLSearchParams({ scriptId: id });
    if (number) params.set("episodeId", String(number));
    if (step !== "episode-split-editor") {
      const position = new URLSearchParams();
      for (const key of scriptReadingParams) {
        const value = query.get(key);
        if (value !== null) position.set(key, value);
      }
      if (position.size) params.set("reader", position.toString());
    }
    navigate(step + "?" + params.toString(), number ?? null);
  };
  const active = showOriginal ? "original" : view === "all" || detached ? "all" : "episodes";
  const reader = docId && <ScriptDocumentReader api={api} projectId={projectId} id={docId} canManage={canManage}
    episode={detached ? undefined : episode?.episode} episodeTitle={detached ? undefined : episode?.title} query={query} change={change} open={open} onImport={toImport}
    onOriginal={(detached ? importedSource : linkedSource) ? toOriginal : undefined}/>;
  return <div className="xj-library">
    <div className="xj-library-toolbar"><nav aria-label={t("sections")}>{([
      ["episodes", "episodes", ListVideo], ["original", "originals", BookOpen], ["all", "all", Files],
    ] as const).map(([key, label, Icon]) => <button type="button" key={key} aria-current={active === key ? "page" : undefined}
      onClick={() => {
        if (key === "original") toOriginal();
        else if (key === "episodes" && firstEpisode) selectEpisode(firstEpisode.episode);
        else change({ ...newLocation, library: key, ...(key === "episodes" ? { scriptId: null, episodeId: null } : {}) });
      }}><Icon size={16}/>{t(label)}</button>)}</nav>
      {canManage && <button type="button" className={scriptButton} onClick={toImport}><FilePlus2 size={16}/>{t("import")}</button>}
    </div>
    {view === "all" ? <div className="xj-library-catalog"><ImportCatalog api={api} projectId={projectId} revision={0} onSelect={selectDocument} location={query} changeLocation={change}/></div> :
      !data ? <><ReadStatus failed={result.failed} retry={result.retry} directory/>{result.failed && explicitId && reader && <section className="xj-reader-paper">{reader}</section>}</> :
      showOriginal ? (book ? <ScriptOriginalReader projectId={projectId} source={book} sources={sources} episode={scopedEpisode} api={episodesApi}
        query={query} change={change} onPlan={item => { if (item.scriptId) open("episode-split-editor", item.scriptId); }} onDocument={selectDocument} canManage={canManage}/> :
        <div className="xj-reader-notice">{t(specificBook ? "missing" : data.permissions?.canReadSources === false ? "unauthorized" : "noBook")}
          {specificBook && sources.length > 0 && <button type="button" className={scriptButton} onClick={toOriginal}>{t("originals")}</button>}</div>) :
      detached ? <section className="xj-reader-paper">{reader}</section> :
      <ReaderLayout kind="episodes" items={directoryItems(data, t)} selected={episode?.episode} query={query} change={change} select={selectEpisode}>
        {episode ? reader || <div className="xj-reader-notice"><small>{t("episode", { number: episode.episode })}</small><h2>{episode.title || t("untitled")}</h2><strong>{t("noScript")}</strong><p>{t("createHelp")}</p>
          <button type="button" className={scriptButton} onClick={() => navigate("episodes", episode.episode)}>{t("openProduction")}</button>
          {linkedSource && <button type="button" className={scriptButton} onClick={toOriginal}>{t("goToSource")}</button>}</div> :
          <div className="xj-reader-notice"><h2>{t(episodeNo ? "missing" : "noEpisodes")}</h2>
            {sources.length ? <button type="button" className={scriptButton} onClick={toOriginal}>{t("originals")}</button> : <p>{t("empty")}</p>}
            {canManage && <button type="button" className={scriptPrimary} onClick={sources.length ? () => navigate("episode-split-editor", null) : toImport}>{t(sources.length ? "plan" : "import")}</button>}</div>}
      </ReaderLayout>}
  </div>;
}

function directoryItems(data: EpisodesSnapshot, t: ReturnType<typeof useLibraryTranslation>["t"]) {
  return data.items.map(item => ({ number: item.episode, title: item.title || t("untitled"), label: t("episode", { number: item.episode }),
    status: t(item.workingScript?.lockedVersionNumber || (!item.workingScript && item.production?.scriptVersionId) ? "frozen" : episodeDocument(item) ? "draft" : "unmade") }));
}
