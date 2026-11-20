import { useEffect, useId, useRef, useState } from "react";
import { ArrowRight, CheckCheck, ListChecks } from "lucide-react";
import { CreatorApiError } from "./api";
import { episodeButton as button, episodeInput as input } from "./EpisodeDetails";
import { useEpisodeProductionTranslation } from "./episode-production-i18n";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";
import { useEpisodeWorkflowTranslation } from "./episode-workflow-i18n";
import type { Episode, EpisodesApi, ProductionCatalog, ProductionCommand } from "./episodes-api";
import { episodeMaterials } from "./episode-workflow";

const targets = ["douyin", "bilibili", "commercial"];
type Operation = "storyboards" | "packages";
type Outcome = { command: ProductionCommand; status: "ready" | "blocked" | "existing" | "running" | "succeeded" | "failed"; error?: string };
const outcomes = (commands: ProductionCommand[]): Outcome[] => commands.map(command => ({
  command, status: command.existingId ? "existing" : command.issue ? "blocked" : "ready", error: command.issue ?? undefined,
}));
const errorCode = (reason: unknown) => reason instanceof CreatorApiError ? reason.code : "NETWORK_ERROR";
function sameCommands(before: Outcome[], after: ProductionCommand[]) {
  return before.length === after.length && before.every(({ command }, index) => {
    const next = after[index];
    return command.episode === next.episode && command.idempotencyKey === next.idempotencyKey && command.confirmationId === next.confirmationId && command.issue === next.issue && command.existingId === next.existingId;
  });
}

interface Props {
  api: EpisodesApi; projectId: string; revision: string; selected: number[]; items: Episode[]; catalog?: ProductionCatalog; disabled: boolean;
  storagePrefix: string; defaultTarget: string; onBusy: (value: boolean) => void; onRefresh: () => void;
  onClear: () => void; onInspect: (episode: number) => void;
}
export function EpisodeBatchActions({ api, projectId, revision, selected, items, catalog, disabled, storagePrefix, defaultTarget, onBusy, onRefresh, onClear, onInspect }: Props) {
  const { t } = useEpisodeWorkflowTranslation();
  const { t: production } = useEpisodeProductionTranslation();
  const { t: handoff } = useEpisodeHandoffTranslation();
  const id = useId();
  const [operation, setOperation] = useState<Operation>("storyboards");
  const [target, setTarget] = useState(() => sessionStorage.getItem(storagePrefix + ":target") || defaultTarget);
  const [custom, setCustom] = useState(() => Boolean(target && !targets.includes(target)));
  const [run, setRun] = useState<"checking" | "running">();
  const [report, setReport] = useState<{ key: string; rows: Outcome[]; executed: boolean }>();
  const [notice, setNotice] = useState<{ key: string; code: string }>();
  const mounted = useRef(false), latch = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const destination = operation === "packages" ? target.trim() : "";
  const key = JSON.stringify([projectId, revision, selected, operation, destination]);
  const rows = report?.key === key ? report.rows : undefined;
  const ready = rows?.filter(row => row.status === "ready").length ?? 0;
  const required = operation === "storyboards" ? ["shot.view", "shot.manage", "script.view", "asset.view"] : ["export.view", "export.manage", "final.view"];
  const allowed = ["project.manage", ...required].every(value => catalog?.permissions.includes(value));
  const canCheck = !disabled && allowed && selected.length > 0 && selected.length <= 50 && (operation === "storyboards" || destination.length > 0);
  function describe(code: string, number?: number) {
    if (code === "EPISODE_STORYBOARD_INPUTS_REQUIRED" && catalog) {
      const episode = items.find(item => item.episode === number);
      if (episode) {
        const material = episodeMaterials(episode, items, catalog);
        if (!material.script) return t("needScript");
        return t(material.snapshots.length ? "needSelection" : "needExtraction");
      }
    }
    if (code === "EPISODE_FINAL_VERSION_REQUIRED") return t("needFilm");
    if (code === "DIRECTOR_CONFIRMATION_REQUIRED") return t("needDirector");
    if (code === "FROZEN_SCRIPT_INVALID") return t("parseHelp");
    const name = code.split(":")[0];
    return handoff(name, { defaultValue: production(name, { defaultValue: production("operationFailed") }) });
  }
  function chooseTarget(value: string) {
    setTarget(value); sessionStorage.setItem(storagePrefix + ":target", value);
  }
  async function submit(execute: boolean) {
    if (latch.current || !canCheck || (execute && (!rows || !ready || report?.executed))) return;
    latch.current = true; setRun("checking"); onBusy(true); setNotice(undefined);
    try {
      const commands = await api.prepare(projectId, revision, selected, operation, destination);
      if (!mounted.current) return;
      if (commands.length !== selected.length || commands.some((command, index) => command.episode !== selected[index])) throw new CreatorApiError(502, "INVALID_RESPONSE", "INVALID_RESPONSE");
      const results = outcomes(commands);
      setReport({ key, rows: results, executed: false });
      if (!execute || !rows) return;
      if (!sameCommands(rows, commands)) { setNotice({ key, code: "checkedAgain" }); return; }
      setRun("running");
      for (const [index, command] of commands.entries()) {
        if (!mounted.current) return;
        if (command.issue || command.existingId) continue;
        results[index] = { command, status: "running" };
        setReport({ key, rows: [...results], executed: true });
        try {
          const response = await api.execute(projectId, command);
          const accepted = command.endpoint === "exports" ? response.delivery?.status === "succeeded" : Boolean(response.shots?.length);
          if (!accepted) throw new CreatorApiError(502, "INVALID_RESPONSE", "INVALID_RESPONSE");
          results[index] = { command, status: "succeeded" };
        } catch (reason) { results[index] = { command, status: "failed", error: errorCode(reason) }; }
        if (mounted.current) setReport({ key, rows: [...results], executed: true });
      }
      if (mounted.current) onRefresh();
    } catch (reason) {
      if (mounted.current) { setReport(undefined); setNotice({ key, code: errorCode(reason) }); }
    } finally {
      latch.current = false;
      if (mounted.current) { setRun(undefined); onBusy(false); }
    }
  }
  return <section aria-labelledby={id} className="space-y-4 border-b border-violet-300/20 bg-violet-300/[.04] p-4 sm:px-5">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 id={id} className="flex items-center gap-2 font-semibold"><ListChecks size={17} aria-hidden="true" className="text-violet-300" />{t("batch")}<span className="text-sm font-normal text-violet-200">{production("selected", { count: selected.length })}</span></h3>
      <button type="button" className={button} disabled={Boolean(run)} onClick={onClear}>{t("clear")}</button></div>
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-xs text-slate-300">{t("batchAction")}<select className={input} disabled={Boolean(run)} value={operation} onChange={e => setOperation(e.target.value as Operation)}>
        <option value="storyboards">{t("batchShots")}</option><option value="packages">{t("batchPackages")}</option></select></label>
      {operation === "packages" && <><label className="text-xs text-slate-300">{production("target")}<select className={input} disabled={Boolean(run)} value={custom ? "other" : target} onChange={e => { const other = e.target.value === "other"; setCustom(other); chooseTarget(other ? "" : e.target.value); }}>
        <option value="">{t("noChoice")}</option>{targets.map(value => <option key={value} value={value}>{t(value)}</option>)}<option value="other">{t("otherTarget")}</option></select></label>
        {custom && <label className="text-xs text-slate-300">{t("targetName")}<input className={input} maxLength={128} value={target} disabled={Boolean(run)} onChange={e => chooseTarget(e.target.value)} /></label>}</>}
      <button type="button" className={button + " inline-flex items-center gap-2"} disabled={!canCheck || Boolean(run)} onClick={() => void submit(false)}><CheckCheck size={16} aria-hidden="true" />{t(run === "checking" ? "checking" : "check")}</button>
    </div>
    <p className="text-xs leading-5 text-slate-400">{selected.length > 50 ? production("limit") : !catalog ? t("loading") : !allowed ? t("noAccess") : disabled && !run ? production("saveFirst") : t(operation === "packages" ? "exportHelp" : "checkHelp")}</p>
    {notice?.key === key && <p role="alert" className="text-sm text-amber-200">{notice.code === "checkedAgain" ? t("checkedAgain") : describe(notice.code)}</p>}
    {rows && <div className="space-y-3 border-t border-white/10 pt-3">
      {!report?.executed && <p role="status" className="flex flex-wrap gap-x-4 gap-y-1 text-sm"><span className="text-violet-200">{t("readyCount", { n: ready })}</span><span className="text-slate-400">{t("skippedCount", { n: rows.filter(item => item.status === "existing").length })}</span><span className="text-amber-200">{t("blockedCount", { n: rows.filter(item => item.status === "blocked").length })}</span></p>}
      <ul className="max-h-64 divide-y divide-white/10 overflow-y-auto" aria-live="polite">{rows.map(({ command, status, error }) => <li key={command.episode} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 text-sm"><div className="min-w-0"><span>{t("episode", { number: command.episode })} · {command.title}</span>{error && <p className="mt-1 text-xs text-amber-200">{describe(error, command.episode)}</p>}</div><span className="flex items-center gap-3 text-xs text-slate-300">{status === "ready" ? t("ready") : production(status)}{(status === "blocked" || status === "failed") && <button type="button" className="text-violet-200 underline underline-offset-4" disabled={Boolean(run)} onClick={() => onInspect(command.episode)}>{t("inspect")}</button>}</span></li>)}</ul>
      {ready > 0 && !report?.executed && <div className="flex flex-wrap items-center gap-3"><button type="button" className={button + " inline-flex items-center gap-2 bg-violet-200 font-semibold text-[#201335]"} disabled={!canCheck || Boolean(run)} onClick={() => void submit(true)}>{t(operation === "storyboards" ? "startShots" : "startPackages", { n: ready })}<ArrowRight size={14} aria-hidden="true" /></button>{operation === "storyboards" && <p className="text-xs text-amber-200">{t("charge")}</p>}</div>}
      {run === "running" && <p role="status" className="text-xs text-violet-200">{t("running")}</p>}
    </div>}
  </section>;
}
