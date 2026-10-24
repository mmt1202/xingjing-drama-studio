import { useCallback, useEffect, useRef, useState } from "react";
import { ActionError, CreatorPage, StatePanel } from "./shared";
import { useSetupTranslation } from "./setup-i18n";
import { useLibraryTranslation } from "./library-i18n";
import { ProjectCard } from "@/xingjing_creator_home/ProjectCard";
import { useHomeTranslation } from "@/xingjing_creator_home/i18n";
import type { CreatorProjectsPort, ProjectSummary } from "./types";

export function ProjectLibraryPage({ api, onNavigate }: { api: CreatorProjectsPort; onNavigate: (target: "create" | "guided-create" | "control", projectId?: string) => void }) {
  const { t } = useSetupTranslation();
  const { t: copy } = useLibraryTranslation();
  const { t: homeText } = useHomeTranslation();
  const [items, setItems] = useState<ProjectSummary[]>(); const [error, setError] = useState<Error>(); const busy = useRef(new Set<string>());
  const load = useCallback(() => { setError(undefined); api.listProjects({ sort: "updatedAt:desc,id:desc" }).then((r) => setItems(r.items)).catch((e: Error) => setError(e)); }, [api]);
  useEffect(() => { void api.listProjects({ sort: "updatedAt:desc,id:desc" }).then((r) => setItems(r.items)).catch((e: Error) => setError(e)); }, [api]);
  const archive = async (p: ProjectSummary) => { if (busy.current.has(p.id)) return; busy.current.add(p.id); try { await api.projectAction(p.id, p.archived ? "restore" : "archive", {}, p.version); load(); } catch (e) { setError(e as Error); } finally { busy.current.delete(p.id); } };
  return <CreatorPage api={api} permission="project.view" title={homeText("projects")} className="xj-project-library">{(context) => <>
    <div className="xj-library-toolbar"><p>{copy("hint")}</p>{context.permissions.includes("project.manage") && <div className="xj-library-actions"><button className="xj-shell-button xj-shell-button--primary" onClick={() => onNavigate("create")}>{t("quick")}</button><button className="xj-shell-button" onClick={() => onNavigate("guided-create")}>{t("guided")}</button></div>}</div>
    {!items && !error && <StatePanel title={copy("loading")} />}
    {error && <ActionError error={error} onRetry={load} />}
    {items?.length === 0 && <StatePanel title={copy("empty")} detail={copy("emptyHint")} action={context.permissions.includes("project.manage") ? homeText("create") : undefined} onAction={() => onNavigate("create")} />}
    {items && items.length > 0 && <section className="xj-workspace-project-grid" aria-label={homeText("results")}>{items.map((project) => <ProjectCard key={project.id} project={project} openLabel={project.setupState === "draft" ? t("resumeDraft") : copy("open")} disabled={project.setupState === "draft" && !context.permissions.includes("project.manage")} onOpen={() => onNavigate(project.setupState === "draft" ? "guided-create" : "control", project.id)} actions={context.permissions.includes("project.manage") && <button className="xj-project-card-secondary" aria-label={copy(project.archived ? "restoreProject" : "archiveProject", { name: project.name })} onClick={() => void archive(project)}>{copy(project.archived ? "restore" : "archive")}</button>} />)}</section>}
  </>}</CreatorPage>;
}

export { ProjectEditorPage } from "./ProjectEditorPage";

export { ProjectControlPage } from "./ProjectControlPage";
