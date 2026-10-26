import { useState } from "react";
import { useScriptExperienceTranslation } from "./script-experience-i18n";
import { scriptButton, type ScriptFrameContext } from "./script-workflow";

export function AiReadinessNotice({ frame }: { frame: ScriptFrameContext }) {
  const { t } = useScriptExperienceTranslation();
  const status = frame.readinessChecking ? "checking" : frame.readinessError || !frame.aiReadiness || frame.aiReadiness.status === "temporarily_unavailable" ? "temporarilyUnavailable" : frame.aiReadiness.ready ? "ready" : "unavailable";
  return <section className="mt-4 rounded-xl border border-white/10 p-4" aria-label={t("readiness")} aria-busy={frame.readinessChecking}>
    <h3 className="text-xs text-slate-400">{t("readiness")}</h3>
    <p role="status" className={"mt-2 text-sm " + (frame.aiReady ? "text-emerald-300" : "text-amber-200")}>{t(status)}</p>
    {!frame.aiReady && !frame.readinessChecking ? <p className="mt-2 text-xs leading-6 text-slate-400">{t("unavailableHelp")}</p> : null}
    <p className="mt-2 text-xs leading-6 text-slate-500">{t("readinessHelp")}</p>
    <button type="button" className={scriptButton + " mt-3"} disabled={frame.readinessChecking} onClick={frame.recheckReadiness}>{t("recheck")}</button>
  </section>;
}

export function ManualScriptFormat() {
  const { t } = useScriptExperienceTranslation();
  const [feedback, setFeedback] = useState<"copied" | "copyFailed">();
  return <details className="mt-5 rounded-xl border border-white/10 p-4">
    <summary className="cursor-pointer text-sm text-violet-200">{t("manualFormat")}</summary>
    <p className="mt-3 text-xs leading-6 text-slate-400">{t("manualFormatHelp")}</p>
    <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg bg-black/20 p-3 font-sans text-sm leading-7 text-slate-300">{t("example")}</pre>
    <button type="button" className={scriptButton + " mt-3"} onClick={() => { if (!navigator.clipboard) { setFeedback("copyFailed"); return; } void navigator.clipboard.writeText(t("example")).then(() => setFeedback("copied"), () => setFeedback("copyFailed")); }}>{t("copyExample")}</button>
    {feedback ? <p role="status" className="mt-2 text-xs text-slate-400">{t(feedback)}</p> : null}
  </details>;
}
