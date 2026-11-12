import { useEffect, useMemo, useState } from "react";
import { createSourcePlanApi, type SourceReference } from "./api";
import { button, card, problemText, useSourcePlanTranslation } from "./i18n";
import { SourceSnapshotReader } from "./SourceSnapshotReader";

export function SourceProvenancePanel({ workspaceId, projectId, scriptId }: { workspaceId: string; projectId: string; scriptId: string }) {
  const { t } = useSourcePlanTranslation();
  const api = useMemo(() => createSourcePlanApi(workspaceId), [workspaceId]);
  const [reload, setReload] = useState(0);
  const key = JSON.stringify([workspaceId, projectId, scriptId, reload]);
  const [state, setState] = useState<{ key: string; sourceRef?: SourceReference | null; error?: unknown }>();
  useEffect(() => {
    const controller = new AbortController();
    api.provenance(projectId, scriptId, controller.signal)
      .then(value => { if (!controller.signal.aborted) setState({ key, sourceRef: value.sourceRef }); })
      .catch(error => { if (!controller.signal.aborted) setState({ key, error }); });
    return () => controller.abort();
  }, [api, projectId, scriptId, key]);
  const current = state?.key === key ? state : undefined;
  return <section className={card + " mt-5"}>
    <h3 className="font-semibold">{t("provenance")}</h3>
    <p className="mt-2 text-sm leading-7 text-slate-400">{t("provenanceHelp")}</p>
    {!current ? <p role="status" className="mt-3 text-sm text-slate-400">{t("loading")}</p>
      : current.error ? <div role="alert" className="mt-3 text-sm text-amber-200"><p>{problemText(current.error, t)}</p><button className={button + " mt-3"} onClick={() => setReload(value => value + 1)}>{t("refresh")}</button></div>
      : current.sourceRef ? <SourceSnapshotReader key={key} api={api} projectId={projectId} reference={current.sourceRef} />
      : <p className="mt-3 text-sm text-slate-400">{t("noProvenance")}</p>}
  </section>;
}
