import { useCallback, useEffect, useRef, useState } from "react";
import { ActionError, CreatorPage, StatePanel } from "./shared";
import { useSetupTranslation } from "./setup-i18n";
import type { CreatorProjectsPort, ProjectSummary } from "./types";

export function ProjectLibraryPage({ api, onNavigate }: { api: CreatorProjectsPort; onNavigate: (target: "create" | "guided-create" | "control", projectId?: string) => void }) {
  const { t } = useSetupTranslation();
  const [items, setItems] = useState<ProjectSummary[]>(); const [error, setError] = useState<Error>(); const busy = useRef(new Set<string>());
  const load = useCallback(() => { setError(undefined); api.listProjects({ sort: "updatedAt:desc,id:desc" }).then((r) => setItems(r.items)).catch((e: Error) => setError(e)); }, [api]);
  useEffect(() => { void api.listProjects({ sort: "updatedAt:desc,id:desc" }).then((r) => setItems(r.items)).catch((e: Error) => setError(e)); }, [api]);
  const archive = async (p: ProjectSummary) => { if (busy.current.has(p.id)) return; busy.current.add(p.id); try { await api.projectAction(p.id, p.archived ? "restore" : "archive", {}, p.version); load(); } catch (e) { setError(e as Error); } finally { busy.current.delete(p.id); } };
  return <CreatorPage api={api} permission="project.view" title="项目库">{(context) => <>{Boolean(items?.length) && context.permissions.includes("project.manage") && <div className="mb-5 flex flex-wrap gap-3"><button className="rounded-xl bg-[#FF6B4A] px-4 py-2.5" onClick={() => onNavigate("create")}>{t("quick")}</button><button className="rounded-xl border border-[#2A2E3A] px-4 py-2.5" onClick={() => onNavigate("guided-create")}>{t("guided")}</button></div>}{!items && !error && <StatePanel title="正在加载项目…" />}{error && <ActionError error={error} onRetry={load} />}{items?.length === 0 && <StatePanel title="这个工作区还没有项目" detail="创建项目后可继续完善配置、导入剧本并进入生产。" action="新建项目" onAction={() => onNavigate("create")} />}{items && items.length > 0 && <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{items.map((p) => <article key={p.id} className="rounded-2xl border border-[#2A2E3A] bg-[#12141B] p-5"><p className="text-xs text-[#7E8494]">{p.type} · {p.targetPlatform}</p><h2 className="mt-1 text-lg font-semibold">{p.name}</h2><p className="mt-2 text-sm text-[#B8BECC]">负责人 {p.ownerName} · {p.setupState === "draft" ? t("draftBadge") : p.productionStatus}</p><div className="mt-4 flex gap-2"><button onClick={() => onNavigate(p.setupState === "draft" ? "guided-create" : "control", p.id)} className="rounded-lg bg-[#FF6B4A] px-3 py-2">{p.setupState === "draft" ? t("resumeDraft") : "进入总控"}</button><button aria-label={`${p.archived ? "恢复" : "归档"}${p.name}`} onClick={() => void archive(p)} className="rounded-lg border border-[#2A2E3A] px-3 py-2">{p.archived ? "恢复" : "归档"}</button></div></article>)}</section>}</>}</CreatorPage>;
}

export { ProjectEditorPage } from "./ProjectEditorPage";

export { ProjectControlPage } from "./ProjectControlPage";
