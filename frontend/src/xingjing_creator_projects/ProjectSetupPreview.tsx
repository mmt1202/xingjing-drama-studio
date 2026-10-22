import { Check, CircleAlert, FolderOpen } from "lucide-react";
import { useSetupTranslation } from "./setup-i18n";
import { missingSetup } from "./setup-state";
import type { ProjectSetup } from "./types";

export function ProjectSetupPreview({ name, value }: { name: string; value: ProjectSetup }) {
  const { t, i18n } = useSetupTranslation();
  const missing = missingSetup(name, value);
  const notes = [
    ...(value.budgetMinor === null ? ["missingBudget"] : []),
    ...(["novel", "script"].includes(value.sourceKind) || value.rightsBasis !== "original" ? ["rightsWarning"] : []),
    ...(value.candidateCount > 3 ? ["candidateWarning"] : []),
    ...(value.subtitleLanguages.length > 1 ? ["languageWarning"] : []),
    ...(!value.imageModelKey || !value.videoModelKey ? ["modelWarning"] : []),
  ];
  return <aside className="space-y-5 lg:sticky lg:top-5 lg:self-start">
    <section className="rounded-2xl border border-orange-300/25 bg-orange-300/[.04] p-5">
      <p className="text-xs font-medium tracking-wide text-orange-300">{t("preview")}</p>
      <h2 className="mt-3 break-words text-xl font-semibold text-white">{name.trim() || t("name")}</h2>
      <p className="mt-2 text-sm text-slate-300">{t(value.projectType)} · {value.aspectRatio} · {t(value.primaryPlatform)}</p>
      <p className="mt-2 text-xs text-slate-400">{t("scale", { episodes: value.episodeCount, candidates: value.candidateCount })}</p>
      <dl className="mt-5 space-y-3 border-t border-white/10 pt-4 text-sm">
        <div className="flex justify-between gap-3"><dt className="text-slate-400">{t("totalDuration")}</dt><dd>{t("minutes", { count: Math.round(value.episodeCount * value.episodeDurationSeconds / 6) / 10 })}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-400">{t("budgetMinor")}</dt><dd>{value.budgetMinor === null ? "—" : new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: "CNY" }).format(value.budgetMinor / 100)}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-400">{t("generationMode")}</dt><dd>{t(value.generationMode)}</dd></div>
      </dl>
    </section>
    <section className="rounded-2xl border border-white/10 bg-[#12141b] p-5">
      <h2 className="text-sm font-semibold">{t("checks")}</h2>
      <ul className="mt-4 space-y-3 text-xs leading-5">{missing.map((key) => <li key={key} className="flex gap-2 text-rose-300"><CircleAlert size={16} className="mt-0.5 shrink-0" />{t(key)}</li>)}
        {missing.length === 0 && <li className="flex gap-2 text-emerald-300"><Check size={16} className="shrink-0" />{t("ready")}</li>}
        {notes.map((key) => <li key={key} className="text-slate-400">{t(key)}</li>)}
      </ul>
    </section>
    <section className="rounded-2xl border border-white/10 bg-[#12141b] p-5">
      <h2 className="text-sm font-semibold">{t("deliverables")}</h2><ul className="mt-4 grid grid-cols-2 gap-3 text-xs text-slate-300">{["masters", "subtitles", "covers", "platforms", "modelRecords", "compliance"].map((key) => <li key={key} className="flex items-center gap-2"><FolderOpen size={14} className="shrink-0 text-orange-300/80" />{t(key)}</li>)}</ul>
      <p className="mt-4 text-xs leading-5 text-slate-500">{t("directoriesHelp")}</p>
    </section>
  </aside>;
}
