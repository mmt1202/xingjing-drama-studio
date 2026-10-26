import { useEffect, useState } from "react";
import { useScriptTranslation } from "./script-i18n";
import { useScriptExperienceTranslation } from "./script-experience-i18n";
import { AiReadinessNotice } from "./ScriptExperience";
import { MutationFeedback } from "./script-views";
import {
  AiLimitNotice, NextStep, ScriptPageFrame, ValidationSummary, aiCharacterLimit, characterCount,
  completeScript, contentOf, currentVersion, scriptButton, scriptField, scriptPanel, scriptPrimary,
  useScriptMutation, type ScriptFrameContext, type ScriptPageProps,
} from "./script-workflow";
import type { DirectorProfile, ScriptAction } from "./types";

const profileFields = ["audience", "pacing", "visualStyle", "cameraLanguage"] as const;
type ProfileField = typeof profileFields[number];
interface DirectorDraft { baseRevision: number; fields: Record<ProfileField, string>; constraints: string }
const toDraft = (profile: DirectorProfile, revision: number): DirectorDraft => ({ baseRevision: revision, fields: { audience: profile.audience ?? "", pacing: profile.pacing ?? "", visualStyle: profile.visualStyle ?? "", cameraLanguage: profile.cameraLanguage ?? "" }, constraints: profile.productionConstraints.join("\n") });
const blankProfile: DirectorProfile = { audience: null, pacing: null, visualStyle: null, cameraLanguage: null, productionConstraints: [] };
const fullProfile = (profile: DirectorProfile) => profileFields.every((field) => Boolean(profile[field]?.trim()));

export function AiDirectorPage(props: ScriptPageProps) {
  const [blocked, setBlocked] = useState(false);
  return <ScriptPageFrame {...props} page="ai-director" blocked={blocked}>{(frame) => <DirectorWorkspace key={frame.script!.id} {...props} frame={frame} onBlocked={setBlocked} />}</ScriptPageFrame>;
}

function DirectorWorkspace({ api, projectId, selectedDirectorCandidateId, onNavigate, frame, onBlocked }: ScriptPageProps & { frame: ScriptFrameContext; onBlocked: (value: boolean) => void }) {
  const { t } = useScriptTranslation(), { t: copy, i18n } = useScriptExperienceTranslation();
  const formatTime = (value: string) => { const date = new Date(value); return Number.isFinite(date.getTime()) ? date.toLocaleString(i18n.resolvedLanguage ?? "zh") : "—"; };
  const script = frame.script!, mutation = useScriptMutation();
  const [draft, setDraft] = useState<DirectorDraft>();
  const [discard, setDiscard] = useState(false);
  const [selection, setSelection] = useState<{ prop: string | undefined; id: string }>();
  const [confirmation, setConfirmation] = useState<{ candidateId: string; baseRevision: number }>();
  const [note, setNote] = useState("");
  const candidates = script.directorCandidates ?? [], latest = candidates.at(-1);
  const selectedId = selection && selection.prop === selectedDirectorCandidateId ? selection.id : selectedDirectorCandidateId ?? latest?.id;
  const selected = candidates.find((item) => item.id === selectedId);
  const hasContract = Array.isArray(script.directorCandidates) && Array.isArray(script.directorConfirmations) && Boolean(script.directorState);
  const busy = Boolean(mutation.pending), locked = !frame.canManage || busy || mutation.uncertain;
  const blocked = Boolean(draft || confirmation || busy || mutation.uncertain);
  const latestSelected = Boolean(selected && selected.id === latest?.id);
  const currentConfirmation = script.directorConfirmations?.find((item) => item.id === script.currentDirectorConfirmationId && item.candidateId === selected?.id);
  const frozenMatch = Boolean(selected && script.lockedVersionNumber && selected.scriptVersionId === `${script.id}@${script.lockedVersionNumber}` && script.directorState !== "stale");
  const canConfirm = Boolean(hasContract && latestSelected && selected && frozenMatch && fullProfile(selected.profile) && !currentConfirmation);
  const constraints = draft?.constraints.split(/\r?\n/).map((value) => value.trim()).filter(Boolean) ?? [];
  const draftValid = Boolean(draft && profileFields.every((field) => characterCount(draft.fields[field]) <= 1000) && constraints.length <= 100 && constraints.every((value) => characterCount(value) <= 2000) && (profileFields.some((field) => draft.fields[field].trim()) || constraints.length));
  const mismatch = Boolean(draft && draft.baseRevision !== script.revision);
  useEffect(() => { onBlocked(blocked); return () => onBlocked(false); }, [blocked, onBlocked]);

  function choose(id: string) {
    if (blocked) return;
    mutation.clear(); setSelection({ prop: selectedDirectorCandidateId, id });
    onNavigate?.("ai-director?" + new URLSearchParams({ scriptId: script.id, directorCandidateId: id }).toString());
  }
  async function act(action: ScriptAction, payload: unknown, revision: number) {
    const receipt = await api.scriptAction(projectId, action, payload, revision, script.id);
    if (!completeScript(receipt) || receipt.id !== script.id || receipt.projectId !== projectId) throw new Error(t("invalidResponse"));
    // Successful idempotent receipts may describe an older revision; display current state.
    const snapshot = api.getScript ? await api.getScript(projectId, script.id) : receipt;
    if (!completeScript(snapshot) || snapshot.id !== script.id || snapshot.projectId !== projectId) throw new Error(t("invalidResponse"));
    return { snapshot, candidateId: receipt.directorCandidates?.at(-1)?.id };
  }
  function accept(result: Awaited<ReturnType<typeof act>>) {
    frame.accept(result.snapshot); setDraft(undefined); setConfirmation(undefined); setNote(""); setDiscard(false);
    if (result.candidateId) setSelection({ prop: selectedDirectorCandidateId, id: result.candidateId });
  }
  function edit(profile: DirectorProfile) {
    if (locked || blocked) return;
    mutation.clear(); setDraft(toDraft(profile, script.revision));
  }
  function save() {
    if (!draft || !draftValid || locked || mismatch || !hasContract) return;
    const payload: DirectorProfile = { ...Object.fromEntries(profileFields.map((field) => [field, draft.fields[field].trim() || null])) as Pick<DirectorProfile, ProfileField>, productionConstraints: constraints };
    void mutation.run("saveDirector", () => act("setDirectorProfile", payload, draft.baseRevision), accept);
  }
  return <section className={scriptPanel}>
    <h2 className="text-lg font-semibold">{copy("directorWorkflow")}</h2><p className="mt-3 text-sm leading-7 text-slate-400">{copy("directorHelp")}</p>
    <p role="status" className="mt-4 text-sm text-violet-200">{copy("directorState")} · {hasContract ? copy(script.directorState!) : copy("unavailableContract")}</p>
    {!script.lockedVersionNumber ? <><p className="mt-3 text-sm text-amber-200">{copy("workingCopyFirst")}</p><ValidationSummary script={script} /></> : <p className="mt-3 text-xs text-slate-400">{t("frozenVersion", { number: script.lockedVersionNumber })}</p>}
    <AiReadinessNotice frame={frame} /><AiLimitNotice script={script} />
    <div className="mt-4 flex flex-wrap gap-3"><button type="button" className={scriptPrimary} disabled={locked || blocked || !hasContract || !frame.aiReady || !script.lockedVersionNumber || characterCount(contentOf(script)) > aiCharacterLimit(script)} onClick={() => void mutation.run("generateDirector", () => act("generateDirector", {}, script.revision), accept)}>{t(mutation.pending === "generateDirector" ? "generatingDirector" : "generateDirector")}</button><button type="button" className={scriptButton} disabled={locked || blocked || !hasContract} onClick={() => edit(blankProfile)}>{copy("startManual")}</button></div>
    {candidates.length ? <label className="mt-5 block text-sm">{copy("candidates")}<select className={scriptField} value={selectedId ?? ""} disabled={blocked} onChange={(event) => choose(event.target.value)}>{selectedId && !selected ? <option value={selectedId}>{copy("unavailableCandidate")}</option> : null}{[...candidates].reverse().map((candidate) => <option key={candidate.id} value={candidate.id}>{copy("candidate", { number: candidate.number })} · {copy(candidate.origin)}{candidate.id === latest?.id ? " · " + copy("latest") : ""}</option>)}</select></label> : <p className="mt-5 text-sm text-slate-400">{copy("noCandidate")}</p>}
    {selectedId && !selected ? <div className="mt-4"><p role="alert" className="text-sm text-amber-200">{copy("unavailableCandidate")}</p>{latest ? <button type="button" className={scriptButton + " mt-3"} disabled={blocked} onClick={() => choose(latest.id)}>{copy("returnLatest")}</button> : null}</div> : null}
    {selected ? <article className="mt-5 rounded-xl border border-white/10 p-4"><h3 className="font-medium">{copy("candidate", { number: selected.number })} · {copy(latestSelected ? "latest" : "historical")}</h3><p className="mt-2 break-all text-xs text-slate-500">{copy("sourceVersion")} · {selected.scriptVersionId}</p><p className="mt-1 text-xs text-slate-500">{copy("createdAt")} · {formatTime(selected.createdAt)}</p><div className="mt-4 grid gap-4 md:grid-cols-2">{[...profileFields, "productionConstraints" as const].map((field) => <div key={field}><h4 className="text-xs text-slate-500">{t(field)}</h4><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7 text-slate-300">{Array.isArray(selected.profile[field]) ? selected.profile[field].join("\n") || t("noResult") : selected.profile[field] || t("noResult")}</p></div>)}</div><button type="button" className={scriptButton + " mt-4"} disabled={locked || blocked || !hasContract} onClick={() => edit(selected.profile)}>{copy(latestSelected ? "editCandidate" : "copyCandidate")}</button>
      {!latestSelected ? <p className="mt-3 text-xs leading-6 text-slate-400">{copy("needsLatest")}</p> : !frozenMatch ? <p className="mt-3 text-xs text-amber-200">{copy(script.lockedVersionNumber ? "confirmStale" : "workingCopyFirst")}</p> : !fullProfile(selected.profile) ? <p className="mt-3 text-xs text-amber-200">{copy("incomplete")}</p> : null}
      {currentConfirmation ? <p role="status" className="mt-4 text-sm text-emerald-300">{copy("confirmedHelp")}</p> : null}
      {confirmation ? <div className="mt-5 rounded-xl border border-violet-300/30 p-4"><p className="font-medium">{copy("confirmReview", { number: selected.number })}</p><p className="mt-2 text-xs leading-6 text-slate-400">{copy("confirmHelp")}</p><label className="mt-3 block text-sm">{copy("confirmationNote")}<textarea className={scriptField} value={note} maxLength={1000} disabled={locked} onChange={(event) => { setNote(event.target.value); mutation.clear(); }} /></label>{confirmation.baseRevision !== script.revision ? <p className="mt-3 text-xs text-amber-200">{t("conflict")}</p> : null}<div className="mt-3 flex flex-wrap gap-3"><button type="button" className={scriptPrimary} disabled={locked || !canConfirm || confirmation.candidateId !== selected.id || confirmation.baseRevision !== script.revision} onClick={() => void mutation.run("confirmDirector", () => act("confirmDirectorProfile", { candidateId: confirmation.candidateId, note: note.trim() }, confirmation.baseRevision), accept)}>{copy(busy ? "confirming" : "confirm")}</button><button type="button" className={scriptButton} disabled={busy || mutation.uncertain} onClick={() => { setConfirmation(undefined); setNote(""); mutation.clear(); }}>{t("keep")}</button></div></div> : canConfirm ? <button type="button" className={scriptPrimary + " mt-4"} disabled={locked || blocked} onClick={() => { mutation.clear(); setConfirmation({ candidateId: selected.id, baseRevision: script.revision }); }}>{copy("confirm")}</button> : null}
    </article> : null}
    {draft ? <form className="mt-5 space-y-4 rounded-xl border border-violet-300/20 p-4" onSubmit={(event) => { event.preventDefault(); save(); }}><h3 className="font-medium">{copy("profileEditor")}</h3><p className="text-xs leading-6 text-slate-400">{copy("draftHelp")}</p><div className="grid gap-4 md:grid-cols-2">{profileFields.map((field) => <label className="text-sm" key={field}>{t(field)}<textarea className={scriptField + " min-h-28"} maxLength={1000} value={draft.fields[field]} disabled={locked} onChange={(event) => { setDraft({ ...draft, fields: { ...draft.fields, [field]: event.target.value } }); mutation.clear(); }} /></label>)}</div><label className="block text-sm">{t("productionConstraints")}<textarea className={scriptField + " min-h-28"} maxLength={200100} value={draft.constraints} disabled={locked} onChange={(event) => { setDraft({ ...draft, constraints: event.target.value }); mutation.clear(); }} /></label><p className="text-xs text-slate-500">{copy("draftLimit")}</p>{mismatch ? <div><p className="text-sm text-amber-200">{copy("draftConflict", { base: draft.baseRevision, current: script.revision })}</p><p className="mt-2 text-xs text-slate-400">{t("rebaseHelp")}</p><button type="button" className={scriptButton + " mt-3"} disabled={locked} onClick={() => { setDraft({ ...draft, baseRevision: script.revision }); mutation.clear(); }}>{t("rebase")}</button></div> : null}<button className={scriptPrimary} disabled={locked || !draftValid || mismatch}>{copy(mutation.pending === "saveDirector" ? "savingCandidate" : "saveCandidate")}</button><div>{discard ? <><p className="text-sm text-amber-200">{t("discardConfirm")}</p><div className="mt-3 flex gap-3"><button type="button" className={scriptButton} disabled={busy || mutation.uncertain} onClick={() => { setDraft(undefined); setDiscard(false); mutation.clear(); }}>{t("discardYes")}</button><button type="button" className={scriptButton} onClick={() => setDiscard(false)}>{t("keep")}</button></div></> : <button type="button" className={scriptButton} disabled={busy || mutation.uncertain} onClick={() => setDiscard(true)}>{copy("discardDraft")}</button>}</div></form> : null}
    <div className="mt-4"><MutationFeedback mutation={mutation} reload={frame.reload} allowRetry={mutation.error?.scope !== "generateDirector" || frame.aiReady || mutation.uncertain} /></div>
    {script.directorConfirmations?.length ? <details className="mt-5 text-sm"><summary className="cursor-pointer text-violet-200">{copy("confirmationHistory")} · {script.directorConfirmations.length}</summary><div className="mt-3 space-y-3">{[...script.directorConfirmations].reverse().map((item) => <article className="rounded-xl border border-white/10 p-3" key={item.id}><p>{copy("candidate", { number: item.candidateNumber })}</p><p className="mt-2 text-xs text-slate-500">{copy("confirmedAt")} · {formatTime(item.confirmedAt)}</p>{item.note ? <p className="mt-2 whitespace-pre-wrap text-sm text-slate-400">{item.note}</p> : null}</article>)}</div></details> : null}
    {script.directorState === "confirmed" ? <NextStep frame={frame} step="director-assets" label="assets" /> : !script.lockedVersionNumber ? <NextStep frame={frame} step={currentVersion(script)?.validationErrors.length ? "source" : "versions"} label={currentVersion(script)?.validationErrors.length ? "openSource" : "openVersions"} /> : <p className="mt-5 text-sm leading-7 text-violet-200">{copy("awaitingDirector")}</p>}<div className="mt-4"><button type="button" className={scriptButton} disabled={!frame.canNavigate || frame.blocked} onClick={() => frame.navigate("episodes")}>{t("openEpisodes")}</button></div>
  </section>;
}
