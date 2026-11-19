import { useRecoveryTranslation } from "./i18n";
import type { Preview } from "./types";

const textBox = "mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/20 p-3 font-sans text-sm leading-6";

export function RecoveryReview({ preview }: { preview: Preview }) {
  const { t } = useRecoveryTranslation();
  const { impact, mappings } = preview;
  return <section className="mt-5 space-y-4 rounded-xl border border-violet-300/25 p-4">
    <h3 className="font-medium">{t("previewTitle")}</h3>
    {preview.changes.map((change, i) => <div key={i}>
      <h4 className="text-sm text-violet-200">{t(change.action)} · {t(change.part)} · {change.targetId === "@start" ? t("start") : change.targetId}</h4>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">{(["before", "after"] as const).map(k => <div key={k} className="min-w-0">
        <span className="text-xs text-slate-400">{t(k)}</span><pre className={textBox}>{change[k] || t("none")}</pre></div>)}</div>
    </div>)}
    <p className="text-sm text-slate-300">{t("mappings", { ...mappings })}</p>
    <details><summary className="cursor-pointer text-sm">{t("candidate")}</summary><pre className={textBox}>{preview.candidate}</pre></details>
    {preview.validation.length ? <div className="text-sm text-amber-200"><p>{t("validation", { count: preview.validation.length })}</p>
      <ul className="mt-2 list-inside list-disc">{preview.validation.map(issue => <li key={issue.path + issue.code}>{t(issue.code)} · {issue.path}</li>)}</ul></div> : null}
    <section className="space-y-2 border-t border-white/10 pt-4 text-sm text-slate-300">
      <h4 className="font-medium text-white">{t("impact")}</h4>
      <p>{t("directors", { count: impact.directorCount })}</p>
      {impact.directors.length ? <ul className="list-inside list-disc">{impact.directors.map(d => <li key={d.version}>v{d.version} · {t(d.confirmed ? "confirmed" : "unconfirmed")}</li>)}</ul> : null}
      <p>{impact.assets === null ? t("assetLabel") + " · " + t("unavailable") : t("assets", { count: impact.assets })}</p>
      <p>{impact.boards === null ? t("boardLabel") + " · " + t("unavailable") : t("boards", { count: impact.boards.count })}</p>
      {impact.boards?.items.length ? <ul className="max-h-36 list-inside list-disc overflow-auto">{impact.boards.items.map(b => <li key={b.id} className="break-words">{b.id} · {b.episode} · v{b.version}</li>)}</ul> : null}
      <p className="leading-6 text-slate-400">{t("oldRefs")}</p>
      {impact.refreshAnalysis ? <p className="text-amber-200">{t("refreshAnalysis")}</p> : null}
      {preview.mode === "fork" && preview.episode ? <p className="text-amber-200">{t("episode", { episode: preview.episode, sequence: preview.workingSequence })}</p> : null}
      {preview.previousWorkingScriptId && preview.previousWorkingScriptId !== preview.scriptId ? <p className="break-all text-xs">{t("priorWorking", { id: preview.previousWorkingScriptId })}</p> : null}
    </section>
  </section>;
}
