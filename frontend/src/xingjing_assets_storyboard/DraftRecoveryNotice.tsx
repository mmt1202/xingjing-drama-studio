import { useProductionHandoffTranslation } from "./handoff-i18n";
import { button } from "./ui";

export function DraftRecoveryNotice({ canResume, busy, onResume, onDiscard, value }: {
  canResume: boolean; busy: boolean; onResume: () => void; onDiscard: () => void; value: unknown;
}) {
  const { t } = useProductionHandoffTranslation();
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "production-draft.json"; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <section role="status" className="mb-5 rounded-xl border border-amber-300/30 bg-amber-300/5 p-4"><p className="text-sm text-amber-100">{t("storedDraft")}</p>{!canResume ? <p className="mt-3 text-xs leading-6 text-slate-400">{t("draftStale")}</p> : null}<p className="mt-3 text-xs leading-6 text-slate-500">{t("discardConfirm")}</p><div className="mt-4 flex flex-wrap gap-2"><button type="button" className={button} disabled={!canResume || busy} onClick={onResume}>{t("resumeDraft")}</button><button type="button" className={button} disabled={busy} onClick={download}>{t("downloadDraft")}</button><button type="button" className={button} disabled={busy} onClick={onDiscard}>{t("discardStored")}</button></div></section>;
}
