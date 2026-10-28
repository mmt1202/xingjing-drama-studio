import { useEffect, useMemo, useRef, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { getTokenForSession } from "@/utils/auth";
import { createAssetsStoryboardApi } from "@/xingjing_assets_storyboard/api";
import type { AssetItem } from "@/xingjing_assets_storyboard/contracts";
import type { GenerationCenterApi } from "./api";
import type { GenerationCandidateSet, GenerationInputReference, GenerationMediaType, GenerationWorkspaceScope } from "./contracts";
import { useSubmissionTranslation } from "./submission-i18n";

const field = "mt-2 block w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm";
const button = "rounded-lg border border-white/10 px-3 py-2 text-xs disabled:opacity-40";
export interface SelectedGenerationInput {
  readonly key: string;
  readonly assetId: string;
  readonly generatedAssetId: string;
  readonly label: string;
  readonly mediaType: GenerationMediaType;
  readonly reference?: GenerationInputReference;
}

export function GenerationInputPicker({ api, scope, mediaType, selected, onChange, disabled }: {
  api: GenerationCenterApi; scope: GenerationWorkspaceScope; mediaType?: GenerationMediaType;
  selected: readonly SelectedGenerationInput[]; onChange: (value: readonly SelectedGenerationInput[]) => void; disabled: boolean;
}) {
  const { t } = useSubmissionTranslation();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const library = useMemo(() => createAssetsStoryboardApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }), [sessionEpoch]);
  const { workspaceId, projectId } = scope;
  const [assets, setAssets] = useState<readonly AssetItem[]>([]);
  const [outputs, setOutputs] = useState<readonly GenerationCandidateSet[]>([]);
  const [nextLibrary, setNextLibrary] = useState<string | null>(null);
  const [outputTotal, setOutputTotal] = useState(0);
  const [loading, setLoading] = useState({ library: true, generated: true });
  const [errors, setErrors] = useState({ library: false, generated: false });
  const [choice, setChoice] = useState("");
  const [reload, setReload] = useState(0);
  const mounted = useRef(false);
  const loadingMore = useRef(false);
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    void library.listAssets({ workspaceId, projectId }, { pageSize: 50 }, abort.signal).then((page) => {
      if (!abort.signal.aborted) { setAssets(page.items); setNextLibrary(page.nextToken); setErrors((value) => ({ ...value, library: false })); }
    }).catch(() => { if (!abort.signal.aborted) setErrors((value) => ({ ...value, library: true })); })
      .finally(() => { if (!abort.signal.aborted) setLoading((value) => ({ ...value, library: false })); });
    void api.candidates(projectId, { limit: 50, mediaType, signal: abort.signal }).then((page) => {
      if (!abort.signal.aborted) { setOutputs(page.candidates); setOutputTotal(page.total); setErrors((value) => ({ ...value, generated: false })); }
    }).catch(() => { if (!abort.signal.aborted) setErrors((value) => ({ ...value, generated: true })); })
      .finally(() => { if (!abort.signal.aborted) setLoading((value) => ({ ...value, generated: false })); });
    return () => { mounted.current = false; abort.abort(); };
  }, [api, library, workspaceId, projectId, mediaType, reload]);

  async function more(kind: "library" | "generated") {
    if (loadingMore.current || disabled) return;
    loadingMore.current = true;
    setLoading((value) => ({ ...value, [kind]: true }));
    try {
      if (kind === "library" && nextLibrary) {
        const page = await library.listAssets(scope, { pageSize: 50, pageToken: nextLibrary });
        if (mounted.current) { setAssets((value) => [...value, ...page.items.filter((item) => !value.some((previous) => previous.id === item.id))]); setNextLibrary(page.nextToken); }
      } else if (kind === "generated") {
        const page = await api.candidates(scope.projectId, { offset: outputs.length, limit: 50, mediaType });
        if (mounted.current) { setOutputs((value) => [...value, ...page.candidates.filter((item) => !value.some((previous) => previous.task.task_id === item.task.task_id))]); setOutputTotal(page.total); }
      }
      if (mounted.current) setErrors((value) => ({ ...value, [kind]: false }));
    } catch { if (mounted.current) setErrors((value) => ({ ...value, [kind]: true })); }
    finally { loadingMore.current = false; if (mounted.current) setLoading((value) => ({ ...value, [kind]: false })); }
  }
  const versions: SelectedGenerationInput[] = assets.flatMap((asset) => asset.versions.flatMap((version) => {
    if (!version.generatedAssetId || !version.generatedMediaType || (mediaType && version.generatedMediaType !== mediaType)) return [];
    return [{ key: `${asset.id}@${version.id}`, assetId: asset.id, generatedAssetId: version.generatedAssetId,
      label: t("version", { name: asset.name, number: version.versionNo }), mediaType: version.generatedMediaType,
      reference: { asset_id: asset.id, asset_version_id: version.id, ...(version.assetContentSha256 ? { asset_content_sha256: version.assetContentSha256 } : {}) } }];
  }));
  const generated: SelectedGenerationInput[] = outputs.flatMap((group) => group.assets.filter((asset) => !mediaType || asset.media_type === mediaType).map((asset, index) => ({
    key: `generated:${asset.asset_id}`, assetId: asset.asset_id, generatedAssetId: asset.asset_id,
    label: t("output", { prompt: [...group.task.request.prompt].slice(0, 48).join(""), number: index + 1 }), mediaType: asset.media_type,
  })));
  const available = [...versions, ...generated].filter((item) => !selected.some((value) => value.assetId === item.assetId || value.generatedAssetId === item.generatedAssetId));
  function add() {
    const item = available.find((value) => value.key === choice);
    if (!disabled && item && selected.length < 12) { onChange([...selected, item]); setChoice(""); }
  }
  return <section className="rounded-xl border border-white/10 p-4" aria-label={t("inputs")}>
    <h3 className="text-sm font-medium">{t("inputs")}</h3><p className="mt-2 text-xs leading-6 text-slate-400">{t("inputHelp")}</p>
    <label className="mt-3 block text-xs text-slate-400">{t("choose")}<select className={field} disabled={disabled} value={choice} onChange={(event) => setChoice(event.target.value)}>
      <option value="">{t("choose")}</option>{([versions, generated] as const).map((group, index) => <optgroup key={index} label={t(index ? "generated" : "catalogue")}>{group.filter((item) => available.some((value) => value.key === item.key)).map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}</optgroup>)}
    </select></label>
    <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={button} disabled={disabled || !available.some((item) => item.key === choice) || selected.length >= 12} onClick={add}>{t("add")}</button>
      {nextLibrary ? <button type="button" className={button} disabled={disabled || loading.library} onClick={() => void more("library")}>{t("catalogue")} · {t(loading.library ? "loading" : "loadMore")}</button> : null}
      {outputs.length < outputTotal ? <button type="button" className={button} disabled={disabled || loading.generated} onClick={() => void more("generated")}>{t("generated")} · {t(loading.generated ? "loading" : "loadMore")}</button> : null}
    </div>
    {loading.library || loading.generated ? <p role="status" className="mt-3 text-xs text-slate-400">{t("loading")}</p> : !versions.length && !generated.length && !errors.library && !errors.generated ? <p className="mt-3 text-xs leading-6 text-amber-200">{t("empty")}</p> : null}
    {errors.library || errors.generated ? <div role="alert" className="mt-3 text-xs text-amber-200">{errors.library ? <p>{t("catalogueError")}</p> : null}{errors.generated ? <p>{t("generatedError")}</p> : null}<button type="button" disabled={disabled || loading.library || loading.generated} className={button + " mt-2"} onClick={() => { setLoading({ library: true, generated: true }); setReload((value) => value + 1); }}>{t("recheck")}</button></div> : null}
    <p className="mt-4 text-xs text-slate-400">{t("selected", { count: selected.length })}</p><ol className="mt-2 space-y-2">{selected.map((item, index) => <li key={item.key} className="flex items-center gap-2 text-xs"><span>{index + 1}.</span><span className="min-w-0 flex-1 break-words">{item.label}</span><button type="button" className={button} disabled={disabled || index === 0} aria-label={t("up", { name: item.label })} onClick={() => { const value = [...selected]; [value[index - 1], value[index]] = [value[index], value[index - 1]]; onChange(value); }}>↑</button><button type="button" className={button} disabled={disabled} aria-label={t("remove", { name: item.label })} onClick={() => onChange(selected.filter((value) => value.key !== item.key))}>×</button></li>)}</ol>
  </section>;
}
