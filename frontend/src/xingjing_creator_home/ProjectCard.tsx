import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { useControlTranslation } from "@/xingjing_creator_projects/control-i18n";
import { useSetupTranslation } from "@/xingjing_creator_projects/setup-i18n";
import { useHomeTranslation } from "./i18n";

interface ProjectCardData {
  id: string;
  name: string;
  type: string;
  targetPlatform: string;
  setupState?: "draft" | "ready";
  productionStatus: string;
  archived?: boolean;
  version: number;
  updatedAt: string;
}

/** Shared project summary; callers retain permissions, navigation and mutations. */
export function ProjectCard({ project, openLabel, onOpen, disabled, actions, headingAs: Heading = "h2" }: {
  project: ProjectCardData; openLabel: string; onOpen: () => void; disabled?: boolean; actions?: ReactNode; headingAs?: "h2" | "h3";
}) {
  const { t, i18n } = useHomeTranslation();
  const { t: setupText } = useSetupTranslation();
  const { t: stageText } = useControlTranslation();
  const draft = project.setupState === "draft";
  const status = project.archived ? "archived" : project.productionStatus;
  const tone = project.archived || draft ? "neutral" : status === "completed" ? "success" : status === "blocked" ? "danger" : "active";
  return <article className="xj-workspace-project-card">
    <div className="xj-project-card-meta">
      <span className="xj-project-card-status" data-tone={tone}><span aria-hidden="true" />{draft && !project.archived ? t("draft") : stageText(`status.${status}`, { defaultValue: status })}</span>
      <span>{setupText(project.type, { defaultValue: project.type })} · {setupText(project.targetPlatform, { defaultValue: project.targetPlatform })}</span>
    </div>
    <Heading className="xj-project-card-title" title={project.name}>{project.name}</Heading>
    <p className="xj-project-card-id" title={project.id}>{project.id} · {t("version", { version: project.version })}</p>
    <footer className="xj-project-card-footer">
      <time dateTime={project.updatedAt}>{new Date(project.updatedAt).toLocaleString(i18n.resolvedLanguage)}</time>
      <div className="xj-project-card-actions"><button type="button" className="xj-project-card-open" disabled={disabled} onClick={onOpen}>{openLabel}<ArrowRight size={14} aria-hidden="true" /></button>{actions}</div>
    </footer>
  </article>;
}
