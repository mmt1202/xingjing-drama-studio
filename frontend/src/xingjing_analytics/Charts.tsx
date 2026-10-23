import { useState } from "react";
import { BarChart3, Clock3, Film, FolderOpen, Layers3 } from "lucide-react";
import { useControlTranslation } from "@/xingjing_creator_projects/control-i18n";
import type { CostAmount, CreatorDashboard } from "./api";
import { analyticsField, analyticsPanel, useAnalyticsTranslation } from "./i18n";
import { attributionLabel } from "./labels";

export function formatMinor(value: string, locale: string) {
  try {
    const [integer, fraction] = value.split(".");
    const decimal = new Intl.NumberFormat(locale).formatToParts(1.1).find((part) => part.type === "decimal")?.value ?? ".";
    return `${BigInt(integer).toLocaleString(locale)}${fraction ? `${decimal}${fraction}` : ""}`;
  }
  catch { return value; }
}

function costComparison(current: string, previous: string | undefined, locale: string): string | null {
  if (previous === undefined || BigInt(previous) <= 0n) return null;
  const scaled = (BigInt(current) - BigInt(previous)) * 10000n / BigInt(previous);
  const absolute = scaled < 0n ? -scaled : scaled;
  const whole = absolute / 100n;
  const fraction = (absolute % 100n).toString().padStart(2, "0");
  return `${scaled > 0n ? "+" : scaled < 0n ? "−" : ""}${formatMinor(`${whole}.${fraction}`, locale)}%`;
}
export function MissingChart() {
  const { t } = useAnalyticsTranslation();
  return <p className="py-8 text-center text-sm leading-6 text-slate-500">{t("noObservation")}</p>;
}

export function DashboardMetrics({ data }: { data: CreatorDashboard }) {
  const { t, i18n } = useAnalyticsTranslation();
  const count = (value: number) => new Intl.NumberFormat(i18n.resolvedLanguage).format(value);
  const restrictedGeneration = !data.coverage.sources.some((source) => ["generation", "audio", "editing"].includes(source));
  const restrictedFinals = data.coverage.notes.includes("finals_restricted") || Boolean(data.filters.modelId || data.filters.memberId);
  const previous = data.comparison?.summary;
  function comparison(current: number | null, prior: number | null | undefined, unit = "") {
    if (!data.comparison || current === null || prior === null || prior === undefined || prior <= 0) return null;
    const difference = current - prior;
    const format = new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 2, signDisplay: "exceptZero" });
    return t("metricComparison", { difference: `${format.format(difference)}${unit}`, percent: `${format.format(difference / prior * 100)}%` });
  }
  const stats = [
    { label: "availableProjects", value: count(data.summary.projects), icon: FolderOpen, note: "currentScope" },
    { label: "renderedShots", value: restrictedFinals ? "—" : count(data.summary.shots), icon: Layers3, note: restrictedFinals ? "unavailableMetric" : "shots_from_final_timelines", comparison: restrictedFinals ? null : comparison(data.summary.shots, previous?.shots) },
    { label: "generatedEpisodes", value: restrictedFinals ? "—" : count(data.summary.episodes), icon: Film, note: restrictedFinals ? "unavailableMetric" : "outputHelp", comparison: restrictedFinals ? null : comparison(data.summary.episodes, previous?.episodes) },
    { label: "averageTaskDuration", value: restrictedGeneration || data.summary.averageTaskDurationMs === null ? "—" : new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 2 }).format(data.summary.averageTaskDurationMs / 60000), icon: Clock3, note: "durationSamples", params: { count: data.summary.taskDurationSamples }, comparison: restrictedGeneration || !data.summary.taskDurationSamples || !previous?.taskDurationSamples ? null : comparison(data.summary.averageTaskDurationMs === null ? null : data.summary.averageTaskDurationMs / 60000, previous.averageTaskDurationMs === null ? null : previous.averageTaskDurationMs / 60000, ` ${t("minutes")}`) },
  ];
  return <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{stats.map(({ label, value, icon: Icon, note, params, comparison: change }) => <section key={label} className={analyticsPanel + " relative overflow-hidden"}>
    <div className="flex items-start justify-between gap-2"><h2 className="text-xs text-slate-400">{t(label)}</h2><Icon size={18} className="text-violet-300/80" /></div>
    <p className="mt-4 text-3xl font-semibold tabular-nums tracking-tight text-white">{value}{label === "averageTaskDuration" && value !== "—" ? <span className="ml-2 text-xs font-normal text-slate-500">{t("minutes")}</span> : null}</p>
    <p className="mt-3 text-xs leading-5 text-slate-500">{t(note, params ?? {})}</p>
    {label !== "availableProjects" && data.comparison ? <p className="mt-2 text-[11px] leading-5 text-violet-200/80">{change ?? t("noBaseline")}</p> : null}
  </section>)}</div>;
}

export function ProductionChart({ data }: { data: CreatorDashboard }) {
  const { t, i18n } = useAnalyticsTranslation();
  const max = Math.max(1, ...data.monthlyProduction.map((item) => item.episodes));
  const allowed = !data.coverage.notes.includes("finals_restricted") && !data.filters.modelId && !data.filters.memberId;
  return <section className={analyticsPanel}>
    <h2 className="text-lg font-semibold">{t("monthly")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("monthlyIndependent")}</p>
    {!allowed ? <p className="py-10 text-sm text-slate-400">{t("unavailableMetric")}</p> : data.monthlyProduction.length ? <ul className="mt-5 grid h-56 grid-flow-col auto-cols-fr items-stretch gap-3" aria-label={t("monthly")}>
      {data.monthlyProduction.map((item) => <li key={item.month} className="flex min-w-0 flex-col items-center gap-2"><span className="text-xs tabular-nums text-slate-300">{item.episodes.toLocaleString(i18n.resolvedLanguage)}</span><div className="flex w-full flex-1 items-end rounded-lg bg-white/[.025]"><div className="w-full rounded-t-md bg-gradient-to-t from-violet-500/65 to-[#FF6B4A]/90" style={{ height: `${item.episodes / max * 100}%` }} /></div><span className="max-w-full truncate text-[11px] text-slate-500">{item.month}</span></li>)}
    </ul> : <MissingChart />}
  </section>;
}

export function ModelChart({ data, select }: { data: CreatorDashboard; select: (modelId: string) => void }) {
  const { t, i18n } = useAnalyticsTranslation();
  return <section className={analyticsPanel}>
    <h2 className="text-lg font-semibold">{t("modelUsage")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("modelHelp")}</p>
    {data.modelUsage.length ? <ul className="mt-5 max-h-80 space-y-4 overflow-y-auto">{data.modelUsage.map((item) => <li key={item.id}><div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm"><button className="min-w-0 truncate rounded text-left text-slate-300 hover:text-orange-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400" title={item.id} onClick={() => select(item.id)}>{attributionLabel(item.id, item.label, t("unknown"))}</button><span className="text-xs tabular-nums text-slate-500">{t("calls", { count: item.tasks })} · {new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 1 }).format(item.share)}%</span></div><div className="h-2 overflow-hidden rounded-full bg-white/[.05]"><div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-orange-400" style={{ width: `${Math.max(0, Math.min(100, item.share))}%` }} /></div></li>)}</ul> : <MissingChart />}
  </section>;
}

export function MemberChart({ data, select }: { data: CreatorDashboard; select: (memberId: string) => void }) {
  const { t } = useAnalyticsTranslation();
  const max = Math.max(1, ...data.memberContributions.map((item) => item.tasks));
  return <section className={analyticsPanel}>
    <h2 className="text-lg font-semibold">{t("contribution")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("contributionHelp")}</p>
    {data.memberContributions.length ? <ul className="mt-5 max-h-80 space-y-4 overflow-y-auto">{data.memberContributions.map((item) => <li key={item.id} className="grid grid-cols-[minmax(5rem,1fr)_minmax(2rem,1.6fr)_auto] items-center gap-3"><button className="truncate rounded text-left text-xs text-slate-300 hover:text-orange-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400" title={item.id} onClick={() => select(item.id)}>{attributionLabel(item.id, item.label, t("unknown"))}</button><div className="h-2.5 overflow-hidden rounded-full bg-white/[.05]"><div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-orange-400" style={{ width: `${item.tasks / max * 100}%` }} /></div><span className="text-xs tabular-nums text-slate-400">{t("taskCount", { count: item.tasks })}</span></li>)}</ul> : <MissingChart />}
  </section>;
}

const colors = ["#A78BFA", "#22C55E", "#4D8DFF", "#FF6B4A", "#F59E0B", "#64748B"];
export function ProjectStatusChart({ data }: { data: CreatorDashboard }) {
  const { t } = useAnalyticsTranslation();
  const { t: stageText } = useControlTranslation();
  const total = data.projectStates.reduce((sum, item) => sum + item.count, 0);
  let start = 0;
  const arcs = data.projectStates.map((item, index) => {
    const end = start + (total ? item.count / total * 360 : 0);
    const arc = `${colors[index % colors.length]} ${start}deg ${end}deg`;
    start = end;
    return arc;
  });
  return <section className={analyticsPanel + " border-violet-300/20 bg-gradient-to-br from-violet-500/[.06] to-transparent"}>
    <h2 className="text-lg font-semibold">{t("projectStates")}</h2>
    {data.projectStates.length ? <><ul className="mt-5 space-y-3">{data.projectStates.map((item, index) => <li key={item.state} className="flex items-center justify-between gap-3 text-sm"><span className="inline-flex min-w-0 items-center gap-2 text-slate-400"><span className="h-2 w-2 shrink-0 rounded-full" style={{ background: colors[index % colors.length] }} />{stageText(`status.${item.state}`, { defaultValue: t(`status_${item.state}`, { defaultValue: t("status_other") }) })}</span><span className="tabular-nums text-slate-200">{item.count}</span></li>)}</ul><div aria-hidden="true" className="mx-auto mt-6 grid h-28 w-28 place-items-center rounded-full" style={{ background: total ? `conic-gradient(${arcs.join(",")})` : "#2A2E3A" }}><div className="grid h-20 w-20 place-items-center rounded-full bg-[#14131F] text-xs tabular-nums text-slate-300">{t("items", { count: total })}</div></div></> : <MissingChart />}
  </section>;
}

export function TaskTrend({ data }: { data: CreatorDashboard }) {
  const { t } = useAnalyticsTranslation();
  const max = Math.max(1, ...data.trend.map((item) => item.succeeded));
  return <section className={analyticsPanel}>
    <div className="flex items-center justify-between gap-3"><h2 className="text-base font-semibold">{t("successTrend")}</h2><BarChart3 size={17} className="text-violet-300" /></div><p className="mt-2 text-xs leading-6 text-slate-500">{t("successTrendHelp")} · {t(data.trendGranularity === "month" ? "trendMonthly" : "trendDaily")}</p>
    {data.trend.length && data.coverage.sources.some((source) => ["generation", "audio", "editing"].includes(source)) ? <div className="mt-4 overflow-x-auto"><ul className="flex h-28 items-stretch gap-1" style={{ minWidth: Math.max(200, data.trend.length * 12) }}>{data.trend.map((item) => <li key={item.date} className="flex min-w-2 flex-1 flex-col justify-end gap-1" title={`${item.date}: ${item.succeeded}`}><span className="sr-only">{item.date}: {t("taskCount", { count: item.succeeded })}</span><div className="flex flex-1 items-end"><div className="w-full rounded-t-sm bg-violet-400/80" style={{ height: `${item.succeeded / max * 100}%` }} /></div></li>)}</ul><div className="mt-2 flex justify-between gap-2 text-[10px] text-slate-500"><span>{data.trend[0].date}</span><span>{data.trend.at(-1)?.date}</span></div></div> : <MissingChart />}
  </section>;
}

export function CostValues({ costs, compact = false }: { costs: CostAmount[]; compact?: boolean }) {
  const { t, i18n } = useAnalyticsTranslation();
  return costs.length ? <ul className={compact ? "space-y-1" : "space-y-3"}>{costs.map((item) => <li key={item.currency} className={compact ? "whitespace-nowrap text-xs tabular-nums text-orange-200" : "rounded-xl border border-white/[.06] bg-[#090B12]/60 p-3"}><p className={compact ? "" : "flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm"}><span className={compact ? "mr-1" : "text-slate-400"}>{item.currency}</span><span className="tabular-nums text-orange-200">{formatMinor(item.actualMinor, i18n.resolvedLanguage ?? i18n.language)}<span className="ml-1 text-[10px] font-normal text-slate-500">{t("minorUnits")}</span></span></p>{!compact ? <p className="mt-2 text-[11px] leading-6 text-slate-500">{t("held")}: {formatMinor(item.heldMinor, i18n.resolvedLanguage ?? i18n.language)} · {t("retryCost")}: {formatMinor(item.retryMinor, i18n.resolvedLanguage ?? i18n.language)} · {t("failedCost")}: {formatMinor(item.failedMinor, i18n.resolvedLanguage ?? i18n.language)}</p> : null}</li>)}</ul> : <span className="text-xs text-slate-500">{t("notAvailable")}</span>;
}

export function CostChart({ data }: { data: CreatorDashboard }) {
  const { t, i18n } = useAnalyticsTranslation();
  const [selection, setSelection] = useState("");
  const canReadBilling = data.coverage.sources.includes("billing") && !data.coverage.notes.includes("costs_restricted");
  const costs = [...data.summary.costs];
  if (canReadBilling && data.comparison) {
    for (const previous of data.comparison.summary.costs) {
      if (!costs.some((item) => item.currency === previous.currency)) {
        costs.push({ currency: previous.currency, actualMinor: "0", heldMinor: "0", failedMinor: "0", retryMinor: "0", dailyActualMinor: data.range.start ? "0.00" : null });
      }
    }
    costs.sort((left, right) => left.currency.localeCompare(right.currency));
  }
  const currency = costs.some((item) => item.currency === selection) ? selection : costs[0]?.currency;
  const values = data.trend.map((item) => ({ date: item.date, amount: item.costs.find((cost) => cost.currency === currency)?.actualMinor ?? (canReadBilling && currency ? "0" : undefined) }));
  let max = 1n;
  for (const item of values) { if (item.amount !== undefined && BigInt(item.amount) > max) max = BigInt(item.amount); }
  return <section className={analyticsPanel}>
    <h2 className="text-base font-semibold">{t("compute")}</h2><p className="mt-2 text-xs leading-6 text-slate-500">{t("computeHelp")}</p>
    <div className="mt-4">{data.coverage.notes.includes("costs_restricted") ? <p className="text-xs leading-6 text-slate-500">{t("costs_restricted")}</p> : costs.length ? <CostValues costs={costs} /> : <p className="py-4 text-xs text-slate-500">{t("noCost")}</p>}</div>
    {!data.coverage.notes.includes("costs_restricted") && costs.length ? <ul className="mt-4 space-y-3 border-t border-[#2A2E3A] pt-4 text-xs leading-6 text-slate-400">{costs.map((item) => {
      const previous = data.comparison?.summary.costs.find((cost) => cost.currency === item.currency);
      const relative = costComparison(item.actualMinor, previous?.actualMinor, i18n.resolvedLanguage ?? i18n.language);
      return <li key={item.currency}><p className="text-[11px] font-medium text-slate-300">{item.currency}</p><p>{t("dailyCost")}: <span className="tabular-nums text-slate-200">{item.dailyActualMinor === null ? "—" : formatMinor(item.dailyActualMinor, i18n.resolvedLanguage ?? i18n.language)}</span>{item.dailyActualMinor === null ? null : <span className="ml-1 text-[10px] text-slate-500">{t("minorUnits")}</span>}</p>{data.comparison ? <p>{t("compare")}: <span className="tabular-nums text-violet-200">{relative ?? "—"}</span>{relative === null ? <span className="ml-1 text-[10px] text-slate-500">{t("noBaseline")}</span> : null}</p> : null}</li>;
    })}</ul> : null}
    {currency ? <><label className="mt-4 block space-y-2 text-xs text-slate-500"><span>{t(data.trendGranularity === "month" ? "monthlyCostTrend" : "costTrend")}</span><select className={analyticsField + " !py-2"} value={currency} onChange={(event) => setSelection(event.target.value)}>{costs.map((item) => <option key={item.currency}>{item.currency}</option>)}</select></label><div className="mt-4 overflow-x-auto"><ul className="flex h-20 items-stretch gap-1" style={{ minWidth: Math.max(200, values.length * 12) }}>{values.map((item) => <li key={item.date} className="flex min-w-2 flex-1 items-end" title={`${item.date}: ${currency} ${item.amount === undefined ? t("noObservation") : formatMinor(item.amount, i18n.resolvedLanguage ?? i18n.language)}`}><span className="sr-only">{item.date}: {currency} {item.amount === undefined ? t("noObservation") : formatMinor(item.amount, i18n.resolvedLanguage ?? i18n.language)} {t("minorUnits")}</span><div className="w-full rounded-t-sm bg-[#FF6B4A]/80" style={{ height: `${item.amount === undefined ? 0 : Number(BigInt(item.amount) * 1000n / max) / 10}%` }} /></li>)}</ul></div></> : null}
  </section>;
}

export function CoverageNotes({ data }: { data: CreatorDashboard }) {
  const { t, i18n } = useAnalyticsTranslation();
  return <section className={analyticsPanel}>
    <h2 className="text-base font-semibold">{t("definitions")}</h2><p className="mt-2 text-xs text-slate-500">{t("sourceCount", { count: data.coverage.sources.length })}</p>
    <ul className="mt-4 space-y-2 text-xs leading-6 text-slate-400">{data.coverage.notes.map((note) => <li key={note}>{i18n.exists(note, { ns: "creatorAnalytics" }) ? t(note) : t("unknownCoverage")}</li>)}</ul>
    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-[11px] text-amber-200/80">{data.coverage.missingAttribution.model ? <span>{t("missingModel", { count: data.coverage.missingAttribution.model })}</span> : null}{data.coverage.missingAttribution.member ? <span>{t("missingMember", { count: data.coverage.missingAttribution.member })}</span> : null}{data.coverage.missingAttribution.episode ? <span>{t("missingEpisode", { count: data.coverage.missingAttribution.episode })}</span> : null}</div>
  </section>;
}
