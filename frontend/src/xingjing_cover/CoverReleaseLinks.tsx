import { ArrowUpRight, Images } from "lucide-react";
import { useSearch } from "wouter";
import { useProjectFlow } from "@/xingjing_creator_projects/ProjectFlowShell";
import { coverButton, useCoverTranslation } from "./i18n";

export function CoverReleaseLinks({ projectVersion, target, selectionId }: { projectVersion?: string; target?: string; selectionId?: string }) {
  const { go } = useProjectFlow();
  const { t } = useCoverTranslation();
  const source = new URLSearchParams(useSearch());
  const query = new URLSearchParams();
  if (projectVersion) query.set("projectVersion", projectVersion);
  if (target) query.set("target", target);
  const planId = source.get("coverPlanId");
  if (planId) query.set("coverPlanId", planId);
  if (selectionId) query.set("coverSelectionId", selectionId);
  return <section className="xj-cover-release-links mb-5 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-violet-300/15 bg-violet-400/[.04] p-4"><div className="min-w-0"><h2 className="flex items-center gap-2 text-sm font-medium"><Images size={17} />{t("releaseCreative")}</h2><p className="mt-2 break-all text-xs leading-6 text-slate-400">{selectionId ? t("selectionContext", { id: selectionId }) : t("releaseCreativeHint")}</p></div><div className="flex flex-wrap gap-2"><button className={coverButton} onClick={() => go(`cover-generate?${query}`)}>{t("titleGenerate")}<ArrowUpRight size={14} /></button><button className={coverButton} onClick={() => go(`cover-title-ab-test?${query}`)}>{t("titleAb")}<ArrowUpRight size={14} /></button></div></section>;
}
