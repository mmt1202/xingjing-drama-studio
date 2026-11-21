import { useCallback } from "react";
import { ArrowRight, BookOpen, FileText } from "lucide-react";
import { nextStep } from "./ScriptImportDocuments";
import { ReadingText, ReadStatus } from "./ScriptReaderLayout";
import { useImportTranslation } from "./import-i18n";
import { useLibraryTranslation } from "./script-library-i18n";
import { positiveNumber, readerPageSize, useLibraryRead, type LibraryChange } from "./script-library-read";
import { matchesScriptRecognition } from "./source-input";
import { completeScript, currentVersion, scriptButton, scriptPrimary } from "./script-workflow";
import type { CreatorProjectsPort, ScriptDocument } from "./types";

interface Props {
  api: CreatorProjectsPort; projectId: string; id: string; episode?: number; episodeTitle?: string; canManage: boolean;
  query: URLSearchParams; change: LibraryChange; open: (step: string, id: string, episode?: number) => void;
  onOriginal?: () => void; onImport: () => void;
}
export function ScriptDocumentReader(props: Props) {
  const { api, projectId, id } = props;
  const load = useCallback(async (signal: AbortSignal) => {
    const result = api.getScript ? await api.getScript(projectId, id, signal) : (await api.listScripts(projectId, { query: id })).items.find(item => item.id === id);
    if (!completeScript(result) || result.id !== id || result.projectId !== projectId) throw new Error("SCRIPT_DOCUMENT_MISMATCH");
    return result;
  }, [api, projectId, id]);
  const result = useLibraryRead(load);
  return result.data ? <DocumentBody {...props} doc={result.data}/> : <ReadStatus failed={result.failed} retry={result.retry}/>;
}

function DocumentBody({ doc, episode, episodeTitle, canManage, query, change, open, onOriginal, onImport }: Props & { doc: ScriptDocument }) {
  const { t } = useLibraryTranslation(), { t: intake } = useImportTranslation();
  const head = currentVersion(doc), requested = query.get("v");
  const version = requested && requested !== "original" ? doc.versions.find(item => item.number === positiveNumber(requested)) : head;
  const original = requested === "original" || !head;
  const historical = original || version?.number !== head?.number;
  const body = original ? doc.sourceDocument.content : version?.sourceContent ?? "";
  const characters = Array.from(body);
  const pages = Math.max(1, Math.ceil(characters.length / readerPageSize));
  const part = Math.min(positiveNumber(query.get("part")) ?? 1, pages);
  const info = matchesScriptRecognition(doc.recognition, doc) ? doc.recognition : undefined;
  const next = nextStep(doc, info, episode);
  const frozen = doc.lockedVersionNumber !== null;
  const primary = frozen ? "ai-director" : next.target;
  const setVersion = (v: string | null) => change({ v, part: null });
  const nextAction = () => primary === "new" ? onImport() : open(primary, primary === "episode-split-editor" ? doc.recoveryOrigin?.original_script_id ?? doc.id : doc.id, primary === "episode-split-editor" ? undefined : episode);
  const namedVersion = (number: number) => t("version", { number }) + " · " + t(number === doc.lockedVersionNumber ? "frozen" : number === head?.number ? "current" : "archived");
  return <>
    <div className="xj-reader-heading"><div><p><FileText size={15}/>{episode ? t("episode", { number: episode }) : t("document")}</p>
      <h2>{episodeTitle || doc.title}</h2><small>{t("characters", { count: characters.length.toLocaleString() })}{version && !original && " · " + namedVersion(version.number)}</small></div>
      <label className="xj-reader-select">{t("history")}<select value={original ? "original" : String(version?.number ?? requested)} onChange={e => setVersion(e.target.value)}>
        {doc.versions.slice().reverse().map(item => <option key={item.number} value={item.number}>{namedVersion(item.number)}</option>)}
        <option value="original">{t(episode ? "originalExcerpt" : "original")}</option>
      </select></label>
    </div>
    {requested && !version && !original ? <div className="xj-reader-notice">{t("missing")} <button type="button" className={scriptButton} onClick={() => setVersion(null)}>{t("latest")}</button></div> : <>
      <p className="xj-reader-note">{historical && head ? t("olderHelp") : frozen ? t("frozenHelp", { number: doc.lockedVersionNumber }) :
        !canManage ? t("readOnly") : next.help ? intake(next.help, { episode }) : t("draftHelp")}</p>
      <div className="xj-reader-actions">
        {historical && head ? <button type="button" className={scriptButton} onClick={() => setVersion(null)}>{t("latest")}</button> :
          (canManage || frozen) && <button type="button" className={scriptPrimary} onClick={nextAction}>{frozen ? t("director") : intake(next.label)}<ArrowRight size={15}/></button>}
        {!historical && !frozen && canManage && next.target !== "source" && <button type="button" className={scriptButton} onClick={() => open("source", doc.id, episode)}>{t("edit")}</button>}
        {(frozen || doc.versions.length > 1) && <button type="button" className={scriptButton} onClick={() => open("versions", doc.id, episode)}>{t(canManage ? "manageVersions" : "otherVersions")}</button>}
        {onOriginal && <button type="button" className={scriptButton} onClick={onOriginal}><BookOpen size={15}/>{t(episode ? "goToSource" : "readChapters")}</button>}
      </div>
      <ReadingText key={doc.id + ":" + String(requested) + ":" + part} text={characters.slice((part - 1) * readerPageSize, part * readerPageSize).join("")}
        page={part} pages={pages} select={page => change({ part: page })}/>
    </>}
  </>;
}
