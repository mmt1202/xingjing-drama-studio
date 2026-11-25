import { useEffect, useRef, useState } from "react";
import { Check, LoaderCircle, Sparkles } from "lucide-react";
import { AssetsStoryboardApiError } from "./api";
import type { AssetItem, AssetsStoryboardPort, WorkspaceProjectScope } from "./contracts";
import { useTurnaroundTranslation } from "./turnaround-i18n";

type Notes = { customViews: string; direction: string };
const fields = ["customViews", "direction"] as const;
const limits: Record<keyof Notes, number> = { customViews: 500, direction: 1000 };

function appendNotes(current: string, extra: string) {
  const lines = current.split("\n").map((line) => line.trim()).filter(Boolean);
  const additions = extra.split("\n").map((line) => line.trim()).filter((line) => line && !lines.includes(line));
  return [current.trimEnd(), ...new Set(additions)].filter(Boolean).join("\n");
}

export function TurnaroundPlanSuggestions({ api, scope, asset, views, notes, disabled, onApply, onReload }: {
  api: AssetsStoryboardPort; scope: WorkspaceProjectScope; asset: AssetItem; views: string[];
  notes: Notes; disabled: boolean; onApply: (next: Notes) => void; onReload: () => void;
}) {
  const { t } = useTurnaroundTranslation();
  const controller = useRef<AbortController | null>(null);
  const [working, setWorking] = useState(false);
  const [proposal, setProposal] = useState<Notes>();
  const [selected, setSelected] = useState<Array<keyof Notes>>([]);
  const [notice, setNotice] = useState<"planFailed" | "planStale" | "planDenied" | "planInProgress" | "planApplied">();
  useEffect(() => () => controller.current?.abort(), []);
  async function suggest() {
    if (disabled || controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setNotice(undefined);
    setWorking(true);
    try {
      const result = await api.assetAction(scope, { action: "draftTurnaroundPlan", targetId: asset.id, version: asset.version,
        payload: { ownerProjectId: asset.projectId, fields: notes, views },
      }, request.signal);
      if (request.signal.aborted) return;
      const plan = result.turnaroundPlan;
      if (plan?.assetVersion !== asset.version || !plan.fields.customViews?.trim() || !plan.fields.direction?.trim()) throw new Error("Incomplete turnaround plan");
      setProposal({ customViews: plan.fields.customViews, direction: plan.fields.direction });
      setSelected([...fields]);
    } catch (error) {
      if (!request.signal.aborted) setNotice(error instanceof AssetsStoryboardApiError && error.code === "ASSET_PROFILE_IN_PROGRESS" ? "planInProgress" :
        error instanceof AssetsStoryboardApiError && error.status === 409 ? "planStale" :
        error instanceof AssetsStoryboardApiError && error.status === 403 ? "planDenied" : "planFailed");
    } finally {
      if (!request.signal.aborted) { controller.current = null; setWorking(false); }
    }
  }
  const next = { ...notes };
  if (proposal) for (const field of selected) next[field] = appendNotes(notes[field], proposal[field]);
  const overLimit = fields.some((field) => next[field].length > limits[field]);
  return <div className="xj-turn-assist">
    <div><button type="button" className="xj-turn-secondary" disabled={disabled || working} onClick={() => void suggest()}>
      {working ? <LoaderCircle size={15} className="animate-spin motion-reduce:animate-none" /> : <Sparkles size={15} />}
      {t(working ? "planWorking" : proposal ? "planAgain" : "planSuggest")}</button><p>{t("planHint")}</p></div>
    {notice && <p role="status">{t(notice)} {notice === "planStale" && <button type="button" className="xj-turn-link" onClick={onReload}>{t("load")}</button>}</p>}
    {proposal && <section className="xj-turn-proposal" aria-label={t("planChoose")}>
      <strong>{t("planChoose")}</strong>
      {fields.map((field) => <label key={field}><input type="checkbox" checked={selected.includes(field)} disabled={working}
        onChange={() => setSelected((previous) => previous.includes(field) ? previous.filter((v) => v !== field) : [...previous, field])} />
        <span><b>{t(field === "customViews" ? "custom" : "planDirection")}</b>{proposal[field]}</span></label>)}
      {overLimit && <p role="status">{t("planLength")}</p>}
      <div><button type="button" className="xj-turn-secondary" disabled={working || disabled || !selected.length || overLimit}
        onClick={() => { onApply(next); setProposal(undefined); setNotice("planApplied"); }}><Check size={15} />{t("planAdd")}</button>
        <button type="button" className="xj-turn-link" disabled={working} onClick={() => { setProposal(undefined); setNotice(undefined); }}>{t("planDismiss")}</button></div>
    </section>}
  </div>;
}
