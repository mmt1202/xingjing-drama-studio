import { useEffect, useMemo, useState } from "react";
import { profileInput } from "./AssetProfileSuggestions";
import { useAssetProfileTranslation } from "./asset-profile-i18n";
import type { AssetItem, AssetsStoryboardPort } from "./contracts";
import { button } from "./ui";

export function AssetProfileLinks({ asset, value, api, workspaceId, onChange }: {
  asset: AssetItem; value: unknown; api: AssetsStoryboardPort; workspaceId: string; onChange: (value: string | string[]) => void;
}) {
  const { t } = useAssetProfileTranslation();
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState("");
  const [next, setNext] = useState<string | null>();
  const [options, setOptions] = useState<readonly AssetItem[]>([]);
  const [linkedNames, setLinkedNames] = useState<Record<string, string>>({});
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [attempt, setAttempt] = useState(0);
  const voice = asset.type === "character";
  const ids = useMemo(() => Array.isArray(value) ? value.filter((id): id is string => typeof id === "string")
    : typeof value === "string" ? value.split(/[,，\s]+/).filter(Boolean) : [], [value]);
  const projectId = asset.projectId;
  useEffect(() => {
    const getAsset = api.getAsset?.bind(api);
    if (!open || !projectId || !getAsset || !ids.length) return;
    const controller = new AbortController();
    void Promise.allSettled(ids.map((id) => getAsset({ workspaceId, projectId }, id, controller.signal))).then((results) => {
      if (!controller.signal.aborted) setLinkedNames(Object.fromEntries(results.flatMap((result) =>
        result.status === "fulfilled" ? [[result.value.id, result.value.name]] : [])));
    });
    return () => controller.abort();
  }, [open, ids, projectId, workspaceId, api]);
  useEffect(() => {
    if (!open || !projectId) return;
    const controller = new AbortController();
    void api.listAssets({ workspaceId, projectId }, { assetType: voice ? "voice" : "character", pageSize: 48, pageToken: cursor || undefined }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setOptions((previous) => cursor ? [...new Map([...previous, ...page.items].map((item) => [item.id, item])).values()] : page.items);
        setNext(page.nextToken);
        setState("ready");
      }).catch(() => { if (!controller.signal.aborted) setState("failed"); });
    return () => controller.abort();
  }, [open, projectId, workspaceId, voice, api, cursor, attempt]);

  function change(ids: string[]) {
    onChange(voice ? ids[0] ?? "" : typeof value === "string" ? ids.join(",") : ids);
  }
  if (!projectId) return null;
  return <details className="text-sm text-slate-300" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="w-fit cursor-pointer">{t(voice ? "voiceLink" : "characterLink")}{ids.length ? ` · ${ids.length}` : ""}</summary>
    {open ? <div className="mt-2">
      <p className="text-xs leading-5">{t("linksHint")}</p>
      {ids.length ? <ul className="mt-2 flex flex-wrap gap-2">{ids.map((id) => <li key={id} className="rounded-lg border border-white/15 px-2 py-1">
        {linkedNames[id] ?? options.find((item) => item.id === id)?.name ?? t("existing")} <button type="button" className="ms-1 text-violet-300 underline" onClick={() => change(ids.filter((item) => item !== id))}>{t("remove")}</button>
      </li>)}</ul> : null}
      {options.length ? <select aria-label={t(voice ? "voiceLink" : "characterLink")} value="" className={profileInput}
        onChange={(event) => { const id = event.target.value; if (id) change(voice ? [id] : [...ids, id]); }}>
        <option value="">{t("choose")}</option>
        {options.filter((option) => !ids.includes(option.id)).map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
      </select> : null}
      <div role="status" className="mt-2 text-xs">{state === "loading" ? t("loading") : state === "failed" ? t("linkFailed") : options.length ? null : t("noLinks")}</div>
      {state === "failed" ? <button type="button" className={button} onClick={() => { setState("loading"); setAttempt((n) => n+1); }}>{t("retry")}</button> : null}
      {state === "ready" && next ? <button type="button" className={button} onClick={() => { setState("loading"); setCursor(next); }}>{t("more")}</button> : null}
    </div> : null}
  </details>;
}
