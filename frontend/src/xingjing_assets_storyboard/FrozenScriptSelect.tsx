import { useEffect, useState } from "react";
import { AssetsStoryboardApiError } from "./api";
import { useAssetExtractionTranslation } from "./asset-extraction-i18n";
import type { AssetsStoryboardPort, FrozenScriptSources, WorkspaceProjectScope } from "./contracts";
import { button } from "./ui";

export interface EpisodeScriptBinding { readonly episode: number; readonly title?: string; readonly scriptVersionId?: string | null }

export function useFrozenScriptSource(
  api: AssetsStoryboardPort, { workspaceId, projectId, episodeId }: WorkspaceProjectScope,
  enabled: boolean, initialVersion?: string, scriptId?: string,
) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ revision: number; catalog?: FrozenScriptSources; error?: unknown }>();
  const [selection, select] = useState<string>();
  const bound = Boolean(episodeId);
  const needsBinding = bound && !initialVersion;
  useEffect(() => {
    if (!enabled || !projectId || needsBinding || !api.getFrozenScriptSources) return;
    const controller = new AbortController();
    void api.getFrozenScriptSources({ workspaceId, projectId }, controller.signal).then(
      (catalog) => { if (!controller.signal.aborted) setResult({ revision, catalog }); },
      (error: unknown) => { if (!controller.signal.aborted) setResult({ revision, error }); },
    );
    return () => controller.abort();
  }, [enabled, projectId, workspaceId, needsBinding, api, revision]);

  const current = result?.revision === revision ? result : undefined;
  const choices = (current?.catalog?.scripts ?? []).filter((item) =>
    bound ? item.id === initialVersion : !scriptId || item.id === `${scriptId}@${item.version}`);
  const preferred = (bound ? initialVersion : selection ?? initialVersion) ?? (choices.length === 1 ? choices[0].id : "");
  const value = choices.some((item) => item.id === preferred) ? preferred : "";
  const error = current?.error;
  let notice: "episodeEmpty" | "loadFailed" | "denied" | "loading" | "stale" | "empty" | undefined;
  if (needsBinding) notice = "episodeEmpty";
  else if (error instanceof AssetsStoryboardApiError && error.status === 403) notice = "denied";
  else if (!api.getFrozenScriptSources || error) notice = "loadFailed";
  else if (!current?.catalog) notice = "loading";
  else if (!choices.length) notice = bound ? "episodeEmpty" : "empty";
  else if (preferred && !value) notice = "stale";
  return { value, choices, bound, notice, canManage: current?.catalog?.canManage, select, reload: () => setRevision((n) => n + 1) };
}

export type FrozenScriptSource = ReturnType<typeof useFrozenScriptSource>;

export function FrozenScriptSelect({ source, episodes, disabled, onReview, onSelect = source.select }: {
  source: FrozenScriptSource; episodes?: readonly EpisodeScriptBinding[]; disabled: boolean; onReview?: () => void;
  onSelect?: (version: string) => void;
}) {
  const { t } = useAssetExtractionTranslation();
  return <div>
    <label className="block text-sm text-[#B8BECC]">{t("source")}
      <select className="mt-2 block w-full rounded-[10px] border border-[#3A3F4E] bg-[#171922] p-3 text-[#F5F6FA]"
        value={source.value} disabled={disabled || source.bound || !source.choices.length}
        onChange={(event) => onSelect(event.target.value)}>
        {!source.value || !source.bound ? <option value="">{t("choose")}</option> : null}
        {source.choices.map((item) => {
          const numbers = episodes?.filter((episode) => episode.scriptVersionId === item.id).map((episode) => episode.episode);
          return <option key={item.id} value={item.id}>{t(numbers?.length ? "episodeVersion" : "version", { ...item, episodes: numbers?.join(", ") })}</option>;
        })}
      </select>
    </label>
    {source.notice ? <p role="status" className="mt-2 text-sm leading-6 text-[#B8BECC]">{t(source.notice)}</p> : null}
    {source.notice === "loadFailed" ? <button type="button" disabled={disabled} className={button + " mt-2"} onClick={source.reload}>{t("retry")}</button> : null}
    {onReview && (source.notice === "episodeEmpty" || source.notice === "empty" || source.notice === "stale") ?
      <button type="button" disabled={disabled} className={button + " mt-2"} onClick={onReview}>{t(source.bound ? "episodes" : "versions")}</button> : null}
  </div>;
}
