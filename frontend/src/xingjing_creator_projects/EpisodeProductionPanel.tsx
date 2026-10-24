import { useCallback, useEffect, useRef, useState } from "react";
import { CreatorApiError } from "./api";
import { episodeButton as button, episodeInput as input } from "./EpisodeDetails";
import { emptyProduction, type Episode, type EpisodeProduction, type EpisodesApi, type ProductionCatalog, type ProductionCommand } from "./episodes-api";
import { useEpisodeProductionTranslation } from "./episode-production-i18n";
import { useEpisodeHandoffTranslation } from "./episode-handoff-i18n";

interface Props {
  api: EpisodesApi; projectId: string; revision: string; items: Episode[]; current?: Episode; storagePrefix?: string;
  selected: number[]; disabled: boolean; onLoaded: (catalog: ProductionCatalog) => void;
  update: (production: EpisodeProduction) => void;
  onNavigate: (target: string) => void; onBusy: (value: boolean) => void;
}
interface BatchResult { episode: number; title: string; status: string; code?: string; requestId?: string }

export function EpisodeProductionPanel({ api, projectId, revision, items, current, selected, disabled, update, onNavigate, onBusy, onLoaded, storagePrefix = `episodes:${projectId}` }: Props) {
  const { t } = useEpisodeProductionTranslation();
  const { t: handoff } = useEpisodeHandoffTranslation();
  const [catalog, setCatalog] = useState<ProductionCatalog>();
  const [error, setError] = useState<Error>();
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BatchResult[]>([]);
  const [target, setTarget] = useState(() => sessionStorage.getItem(`${storagePrefix}:target`) ?? "");
  const mounted = useRef(false);
  const executing = useRef(false);
  const readSequence = useRef(0);
  const refresh = useCallback(() => {
    const sequence = ++readSequence.current;
    return api.production(projectId).then((data) => {
      if (mounted.current && sequence === readSequence.current) { setCatalog(data); setError(undefined); onLoaded(data); }
    }).catch((reason: Error) => { if (mounted.current && sequence === readSequence.current) setError(reason); });
  }, [api, projectId, onLoaded]);
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; readSequence.current += 1; }; }, [refresh, revision]);
  const has = (...values: string[]) => values.every((value) => catalog?.permissions.includes(value));
  const binding = current?.production ?? emptyProduction();
  const currentState = catalog?.items.find((item) => item.episode === current?.episode);
  const fresh = catalog?.revision === revision && !error;
  const inputsLocked = !!currentState?.boards.length;
  const editDisabled = disabled || running || !fresh || !has("project.manage");
  const canBatch = !disabled && !running && fresh && selected.length > 0 && selected.length <= 50 && has("project.manage");
  function showError(reason: unknown) { return reason instanceof CreatorApiError ? reason.code : "NETWORK_ERROR"; }
  function errorText(code: string) { return handoff(code.split(":", 1)[0], { defaultValue: t(code.split(":", 1)[0], { defaultValue: t("operationFailed") }) }); }

  async function batch(action: "storyboards" | "packages") {
    if (!canBatch || executing.current) return;
    executing.current = true; setRunning(true); onBusy(true); setError(undefined); setResults([]);
    try {
      const commands = await api.prepare(projectId, revision, selected, action, target.trim());
      const outcome: BatchResult[] = commands.map((command) => ({ episode: command.episode, title: command.title, status: command.existingId ? "existing" : command.issue ? "blocked" : "queued", code: command.issue ?? undefined }));
      if (mounted.current) setResults([...outcome]);
      for (const [index, command] of commands.entries()) {
        if (!mounted.current) break;
        if (command.existingId || command.issue) continue;
        outcome[index] = { ...outcome[index], status: "running" }; setResults([...outcome]);
        try {
          const response = await api.execute(projectId, command);
          if (!validResult(command, response)) throw new CreatorApiError(502, "INVALID_RESPONSE", "INVALID_RESPONSE", undefined, true);
          outcome[index] = { ...outcome[index], status: "succeeded" };
        } catch (reason) {
          outcome[index] = { ...outcome[index], status: "failed", code: showError(reason), requestId: reason instanceof CreatorApiError ? reason.requestId : undefined };
        }
        if (mounted.current) setResults([...outcome]);
      }
      if (mounted.current) await refresh();
    } catch (reason) { if (mounted.current) setError(reason as Error); }
    finally { executing.current = false; if (mounted.current) { setRunning(false); onBusy(false); } }
  }
  const link = (page: string) => `${page}?${new URLSearchParams({ episodeId: String(current?.episode ?? ""), ...(current?.workingScript ? { scriptId: current.workingScript.scriptId } : {}), ...(binding.scriptVersionId ? { scriptVersionId: binding.scriptVersionId } : {}), ...(binding.assetSnapshotId ? { assetSnapshotId: binding.assetSnapshotId } : {}), ...(binding.finalVideoVersionId ? { projectVersion: binding.finalVideoVersionId } : {}), ...(target.trim() ? { target: target.trim() } : {}) }).toString()}`;
  const pending = catalog?.items.filter((item) => item.state === "pending_review").length;
  const packaged = catalog?.items.filter((item) => item.deliveries.some((delivery) => delivery.status === "succeeded" && delivery.format === "publish_package")).length;
  return <section className="overflow-hidden rounded-xl border border-[#2A2E3A] bg-[#12141B]">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#2A2E3A] px-5 py-4">
      <div><h2 className="font-semibold">{t("title")}</h2><p className="mt-1 text-xs leading-5 text-slate-400">{t("intro")}</p></div>
      <div className="flex gap-5 text-xs text-slate-400"><span>{t("pending")} <strong className="ml-1 text-base text-amber-200">{fresh && has("compliance.view") ? pending : "—"}</strong></span><span>{t("packaged")} <strong className="ml-1 text-base text-emerald-300">{fresh && has("export.view") ? packaged : "—"}</strong></span></div>
    </div>
    {error && <div role="alert" className="m-5 rounded-lg border border-amber-700/50 p-3 text-sm text-amber-200"><p>{t("loadError")}</p><p className="mt-1 font-mono text-xs">{showError(error)}</p><button className={`${button} mt-3`} disabled={running} onClick={() => void refresh()}>{t("refresh")}</button></div>}
    {!catalog && !error && <p className="p-5 text-sm text-slate-400">{t("loading")}</p>}
    {catalog && <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-4 p-5 lg:border-r lg:border-[#2A2E3A]">
        <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-medium">{current ? t("episode", { number: current.episode }) : t("chooseEpisode")}</h3><button disabled={running || disabled} className={button} onClick={() => void refresh()}>{t("refresh")}</button></div>
        {current && <>
          <label className="block text-xs text-slate-400">{t("script")}<select className={input} value={binding.scriptVersionId} disabled={editDisabled || inputsLocked || !has("script.view")} onChange={(event) => update({ ...binding, scriptVersionId: event.target.value, assetSnapshotId: "" })}>
            <option value="">{t("unbound")}</option>{binding.scriptVersionId && !catalog.scripts.some((s) => s.id === binding.scriptVersionId) && <option value={binding.scriptVersionId}>{t("unavailable")} · {binding.scriptVersionId}</option>}
            {catalog.scripts.map((script) => <option key={script.id} value={script.id} disabled={items.some((item) => item.episode !== current.episode && item.production?.scriptVersionId === script.id) || items.some((item) => item.workingScript && script.id.startsWith(`${item.workingScript.scriptId}@`) && (item.episode !== current.episode || !item.workingScript.rangeMatches))}>{script.title} · v{script.version}</option>)}
          </select></label>
          {current.workingScript?.rangeMatches && current.workingScript.lockedVersionNumber && binding.scriptVersionId !== `${current.workingScript.scriptId}@${current.workingScript.lockedVersionNumber}` ? <button type="button" className={button} disabled={editDisabled || inputsLocked || !has("script.view")} onClick={() => update({ ...binding, scriptVersionId: `${current.workingScript!.scriptId}@${current.workingScript!.lockedVersionNumber}`, assetSnapshotId: "" })}>{handoff("bindWorking")}</button> : null}
          <label className="block text-xs text-slate-400">{t("snapshot")}<select className={input} value={binding.assetSnapshotId} disabled={editDisabled || inputsLocked || !binding.scriptVersionId || !has("asset.view")} onChange={(event) => update({ ...binding, assetSnapshotId: event.target.value })}>
            <option value="">{t("unbound")}</option>{binding.assetSnapshotId && !catalog.snapshots.some((s) => s.id === binding.assetSnapshotId && s.scriptVersionId === binding.scriptVersionId && s.valid) && <option value={binding.assetSnapshotId}>{t("unavailable")} · {binding.assetSnapshotId}</option>}
            {catalog.snapshots.filter((snapshot) => snapshot.scriptVersionId === binding.scriptVersionId && snapshot.valid).map((snapshot) => <option key={snapshot.id} value={snapshot.id}>{snapshot.id} · {t("assets", { count: snapshot.assets.length })}</option>)}
          </select></label>
          <label className="block text-xs text-slate-400">{t("final")}<select className={input} value={binding.finalVideoVersionId} disabled={editDisabled || !has("final.view")} onChange={(event) => update({ ...binding, finalVideoVersionId: event.target.value })}>
            <option value="">{t("unbound")}</option>{binding.finalVideoVersionId && !catalog.finals.some((final) => final.id === binding.finalVideoVersionId && final.episodeId === String(current.episode)) && <option value={binding.finalVideoVersionId}>{t("unavailable")} · {binding.finalVideoVersionId}</option>}
            {catalog.finals.filter((final) => final.episodeId === String(current.episode)).map((final) => <option key={final.id} value={final.id}>{final.id} · {new Date(final.createdAt).toLocaleString()}</option>)}
          </select></label>
          <p className="text-xs leading-5 text-slate-400">{inputsLocked ? t("locked") : t("bindingHelp")}</p>
          {currentState && <div className="space-y-2 text-xs"><p className="text-[#FF9B83]">{t(currentState.state)} · {t("shots", { count: currentState.boards.reduce((sum, board) => sum + board.shots, 0) })}</p>{currentState.issue && <p className="text-amber-300">{errorText(currentState.issue)}</p>}{currentState.deliveries.map((delivery) => <p key={delivery.id} className="break-all text-slate-400">{delivery.target} · {delivery.format} · {delivery.status} · {delivery.id}</p>)}</div>}
          <div className="flex flex-wrap gap-2"><button className={button} disabled={disabled || running || !binding.scriptVersionId} onClick={() => onNavigate(link("assets-extract"))}>{handoff("openAssets")} →</button><button className={button} disabled={disabled || running} onClick={() => onNavigate(link("shots"))}>{t("openBoards")} →</button><button className={button} disabled={disabled || running || !binding.finalVideoVersionId || !target.trim()} onClick={() => onNavigate(link("release-package"))}>{t("openDelivery")} →</button></div>
        </>}
      </div>
      <div className="space-y-4 border-t border-[#2A2E3A] p-5 lg:border-t-0">
        <h3 className="text-sm font-medium">{t("selected", { count: selected.length })}</h3><p className="text-xs leading-6 text-slate-400">{t("batchHelp")}</p>
        <button className={`${button} bg-[#FF6B4A] font-medium text-[#1B100C]`} disabled={!canBatch || !has("shot.manage", "shot.view", "script.view", "asset.view")} onClick={() => void batch("storyboards")}>{t("generate")}</button>
        <div className="border-t border-[#2A2E3A] pt-4"><label className="block text-xs text-slate-400">{t("target")}<input className={input} maxLength={128} value={target} placeholder={t("targetExample")} disabled={running} onChange={(event) => { setTarget(event.target.value); sessionStorage.setItem(`${storagePrefix}:target`, event.target.value); }} /></label><p className="my-3 text-xs leading-6 text-slate-400">{t("packageHelp")}</p><button className={button} disabled={!canBatch || !target.trim() || !has("export.manage", "export.view", "final.view")} onClick={() => void batch("packages")}>{t("createPackages")}</button></div>
        {disabled && <p className="text-xs text-amber-200">{t("saveFirst")}</p>}
        {selected.length > 50 && <p className="text-xs text-amber-200">{t("limit")}</p>}
        {!!results.length && <ol aria-live="polite" className="max-h-72 space-y-3 overflow-y-auto border-t border-[#2A2E3A] pt-4">{results.map((result) => <li key={result.episode} className="text-xs"><div className="flex justify-between gap-3"><strong className="truncate">{result.episode} · {result.title}</strong><span className={result.status === "succeeded" || result.status === "existing" ? "text-emerald-300" : "text-amber-200"}>{t(result.status)}</span></div>{result.code && <p className="mt-1 break-all text-slate-400">{errorText(result.code)} <span className="font-mono">({result.code})</span></p>}{result.requestId && <p className="mt-1 font-mono text-slate-500">{result.requestId}</p>}</li>)}</ol>}
      </div>
    </div>}
  </section>;
}

function validResult(command: ProductionCommand, response: Awaited<ReturnType<EpisodesApi["execute"]>>) {
  return command.endpoint === "exports" ? response.delivery?.status === "succeeded" : Array.isArray(response.shots) && response.shots.length > 0;
}
