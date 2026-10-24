import { useEffect, useRef, useState } from "react";
import { CreatorApiError } from "./api";
import type { Episode, EpisodeSource, EpisodesApi, EpisodesSnapshot } from "./episodes-api";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";
import { episodeButton as button } from "./EpisodeDetails";

interface Props {
  api: EpisodesApi; projectId: string; revision: string; episode?: Episode; sources: EpisodeSource[];
  disabled: boolean; canCreate: boolean; onBusy: (value: boolean) => void;
  onAccepted: (snapshot: EpisodesSnapshot) => void;
  onNavigate: (target: string) => void;
}
export function EpisodeWorkingScriptPanel({ api, projectId, revision, episode, sources, disabled, canCreate, onBusy, onAccepted, onNavigate }: Props) {
  const { t } = useEpisodeHandoffTranslation();
  const [error, setError] = useState<unknown>(), [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false);
  const [reconciled, setReconciled] = useState(false);
  const mounted = useRef(false), latch = useRef(false);
  const retry = useRef<(() => Promise<void>) | undefined>(undefined);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const working = episode?.workingScript;
  const source = sources.find((item) => item.path === episode?.sourceRange?.source_file);
  const range = episode?.sourceRange;
  const signature = JSON.stringify([projectId, episode?.episode, revision, source?.path, source?.digest, range?.start, range?.end]);
  const currentSignature = useRef(signature);
  useEffect(() => { currentSignature.current = signature; }, [signature]);
  const validRange = Boolean(range && source && range.start >= 0 && range.end > range.start && range.end <= source.length);
  const create = async (previousId?: string) => {
    if (!episode || !source || latch.current || disabled || !canCreate || !validRange) return;
    const number = episode.episode;
    const operation = async () => {
      if (latch.current) return;
      latch.current = true; setBusy(true); onBusy(true); setError(undefined); setReconciled(false);
      try {
        const result = await api.createWorkingScript(projectId, number, revision, source.digest, previousId);
        const next = result.workingScript;
        if (!next || !next.scriptId || !Number.isSafeInteger(next.revision) || !Number.isSafeInteger(next.versionNumber) || next.sourceRef?.sourceId !== source.path || next.sourceRef.digest !== source.digest || next.sourceRef.start !== range?.start || next.sourceRef.end !== range.end || !result.revision) throw new Error(t("invalidResponse"));
        if (!mounted.current) return;
        const snapshot = await api.list(projectId);
        if (!snapshot.revision || !Array.isArray(snapshot.items) || !snapshot.items.some((item) => item.episode === number)) throw new Error(t("invalidResponse"));
        if (mounted.current) { setReconciled(currentSignature.current !== signature); onAccepted(snapshot); setConfirm(false); retry.current = undefined; }
      } catch (reason) { if (mounted.current) setError(reason); }
      finally { latch.current = false; if (mounted.current) { setBusy(false); onBusy(false); } }
    };
    retry.current = operation; await operation();
  };
  const link = (page: string) => `${page}?${new URLSearchParams({ episodeId: String(episode?.episode), ...(working ? { scriptId: working.scriptId } : {}), ...(episode?.production?.scriptVersionId ? { scriptVersionId: episode.production.scriptVersionId } : {}), ...(episode?.production?.assetSnapshotId ? { assetSnapshotId: episode.production.assetSnapshotId } : {}) }).toString()}`;
  return <section className="rounded-xl border border-[#2A2E3A] bg-[#12141B] p-5"><h2 className="font-semibold">{t("workingTitle")}</h2><p className="mt-3 text-xs leading-6 text-slate-400">{t("workingHelp")}</p>{!episode ? <p className="mt-4 text-sm text-slate-400">{t("noSelection")}</p> : <>
    {working ? <div className="mt-4 space-y-2"><p className="break-all font-mono text-xs text-slate-400">{working.scriptId}</p><p className="text-sm text-violet-200">{t("workingVersion", { number: working.versionNumber, revision: working.revision })}{working.lockedVersionNumber ? ` · ${t("workingFrozen", { number: working.lockedVersionNumber })}` : ""}</p><p className="text-xs text-slate-400">{t(working.validationState === "valid" ? "workingValid" : "workingInvalid")}</p>{!working.rangeMatches ? <p className="text-xs leading-6 text-amber-200">{t("workingMismatch")}</p> : null}<div className="flex flex-wrap gap-2 pt-2">{[["source", "openWorking"], ["script-lab", "openParse"], ["versions", "openFreeze"]].map(([page, label]) => <button type="button" key={page} className={button} disabled={disabled || busy} onClick={() => onNavigate(link(page))}>{t(label)}</button>)}{episode.production?.scriptVersionId ? <button type="button" className={button} disabled={disabled || busy} onClick={() => onNavigate(link("assets-extract"))}>{t("openAssets")}</button> : null}</div></div> : <p className="mt-4 text-sm text-slate-400">{t("workingEmpty")}</p>}
    {!canCreate ? <p className="mt-4 text-xs text-amber-200">{t("workingPermission")}</p> : !validRange ? <p className="mt-4 text-xs text-amber-200">{t("workingSourceMissing")}</p> : null}
    {disabled ? <p className="mt-3 text-xs text-slate-500">{t("workingSaveFirst")}</p> : null}
    {!working || !working.rangeMatches ? confirm ? <div className="mt-4 rounded-xl border border-amber-400/20 p-4"><p className="text-sm text-amber-100">{t("workingConfirm")}</p><div className="mt-3 flex gap-2"><button type="button" className={button} disabled={busy || disabled || !canCreate} onClick={() => void create(working?.scriptId)}>{t(busy ? "workingCreating" : "confirm")}</button><button type="button" className={button} disabled={busy} onClick={() => setConfirm(false)}>{t("cancel")}</button></div></div> : <button type="button" className={button + " mt-4 border-violet-300/40 text-violet-200"} disabled={disabled || busy || !canCreate || !validRange || Boolean(episode.production?.scriptVersionId)} onClick={() => working ? setConfirm(true) : void create()}>{t(busy ? "workingCreating" : working ? "workingNewCandidate" : "workingCreate")}</button> : null}
    {reconciled ? <p role="status" className="mt-4 text-xs leading-6 text-slate-400">{t("reconciled")}</p> : null}
    {error ? <div role="alert" className="mt-4 rounded-lg border border-amber-300/20 p-3 text-sm text-amber-200"><p>{error instanceof CreatorApiError ? t(error.code, { defaultValue: error.message }) : error instanceof Error ? error.message : t("failed")}</p>{error instanceof CreatorApiError ? <p className="mt-2 break-all font-mono text-xs">{error.code}{error.requestId ? ` · ${t("requestId", { id: error.requestId })}` : ""}</p> : null}<button type="button" className={button + " mt-3"} disabled={busy || disabled} onClick={() => void retry.current?.()}>{t("retry")}</button></div> : null}
  </>}</section>;
}
