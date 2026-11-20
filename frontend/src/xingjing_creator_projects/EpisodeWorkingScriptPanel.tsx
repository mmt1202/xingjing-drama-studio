import { useEffect, useRef, useState } from "react";
import { CreatorApiError } from "./api";
import type { Episode, EpisodeSource, EpisodesApi, EpisodesSnapshot } from "./episodes-api";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";
import { episodeButton as button } from "./EpisodeDetails";
import { useEpisodeWorkflowTranslation } from "./episode-workflow-i18n";

interface Props {
  api: EpisodesApi; projectId: string; revision: string; episode: Episode; sources: EpisodeSource[];
  disabled: boolean; canCreate: boolean; onBusy: (value: boolean) => void;
  onAccepted: (snapshot: EpisodesSnapshot) => void;
  primaryClass: string;
}
export function EpisodeWorkingScriptAction({ api, projectId, revision, episode, sources, disabled, canCreate, onBusy, onAccepted, primaryClass }: Props) {
  const { t } = useEpisodeHandoffTranslation();
  const { t: flow } = useEpisodeWorkflowTranslation();
  const [error, setError] = useState<unknown>(), [confirm, setConfirm] = useState(false), [busy, setBusy] = useState(false);
  const mounted = useRef(false), latch = useRef(false);
  const retry = useRef<(() => Promise<void>) | undefined>(undefined);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const working = episode.workingScript;
  const source = sources.find((item) => item.path === episode.sourceRange?.source_file);
  const range = episode.sourceRange;
  const validRange = Boolean(range && source && range.start >= 0 && range.end > range.start && range.end <= source.length);
  const create = async (previousId?: string) => {
    if (!source || latch.current || disabled || !canCreate || !validRange) return;
    const number = episode.episode;
    let received = false;
    const operation = async () => {
      if (latch.current) return;
      latch.current = true; setBusy(true); onBusy(true); setError(undefined);
      try {
        if (!received) {
          const result = await api.createWorkingScript(projectId, number, revision, source.digest, previousId);
          const next = result.workingScript;
          if (!next || !next.scriptId || !Number.isSafeInteger(next.revision) || !Number.isSafeInteger(next.versionNumber) || next.sourceRef?.sourceId !== source.path || next.sourceRef.digest !== source.digest || next.sourceRef.start !== range?.start || next.sourceRef.end !== range.end || !result.revision) throw new Error(t("invalidResponse"));
          received = true;
        }
        if (!mounted.current) return;
        const snapshot = await api.list(projectId);
        if (!snapshot.revision || !Array.isArray(snapshot.items) || !snapshot.items.some((item) => item.episode === number)) throw new Error(t("invalidResponse"));
        if (mounted.current) { onAccepted(snapshot); setConfirm(false); retry.current = undefined; }
      } catch (reason) { if (mounted.current) setError(reason); }
      finally { latch.current = false; if (mounted.current) { setBusy(false); onBusy(false); } }
    };
    retry.current = operation; await operation();
  };
  if (error) return <div role="alert" className="space-y-2 text-sm text-amber-200">
    <p>{error instanceof CreatorApiError ? t(error.code, { defaultValue: t("failed") }) : t("failed")}</p>
    <button type="button" className={button} disabled={busy || disabled} onClick={() => void retry.current?.()}>{t("retry")}</button>
  </div>;
  if (confirm) return <div className="max-w-sm space-y-2"><p className="text-sm text-amber-100">{t("workingConfirm")}</p>
    <div className="flex gap-2"><button type="button" className={primaryClass} disabled={busy || disabled || !canCreate} onClick={() => void create(working?.scriptId)}>{t(busy ? "workingCreating" : "confirm")}</button>
      <button type="button" className={button} disabled={busy} onClick={() => setConfirm(false)}>{t("cancel")}</button></div>
  </div>;
  return <button type="button" className={primaryClass} disabled={disabled || busy || !canCreate || !validRange || episode.locked || Boolean(episode.production?.scriptVersionId)}
    onClick={() => working ? setConfirm(true) : void create()}>{busy ? t("workingCreating") : flow(working ? "replaceDraft" : "createDraft")}</button>;
}
