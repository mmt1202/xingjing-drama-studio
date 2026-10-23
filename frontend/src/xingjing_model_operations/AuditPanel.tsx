import { useEffect, useState } from "react";
import { useLocation, useSearch } from "wouter";
import type { ModelOperationsApi } from "./api";
import { errorKey } from "./ModelForms";
import { modelButton, modelField, useModelTranslation } from "./i18n";

export function AuditPanel({ api }: { api: ModelOperationsApi }) {
  const { t, i18n } = useModelTranslation(); const [location, navigate] = useLocation(); const search = useSearch(); const params = new URLSearchParams(search);
  const query = new URLSearchParams({ q: params.get("auditQ") ?? "", days: params.get("auditDays") ?? "0", offset: params.get("auditOffset") ?? "0" }).toString();
  const [page, setPage] = useState<Awaited<ReturnType<ModelOperationsApi["audit"]>>>(); const [loaded, setLoaded] = useState(""); const [error, setError] = useState<unknown>(); const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void api.audit(query, abort.signal).then(value => { if (!abort.signal.aborted) { setPage(value); setLoaded(query); setError(undefined); } }).catch((problem: unknown) => { if (!abort.signal.aborted) setError(problem); });
    return () => abort.abort();
  }, [api, query, reload]);
  function filter(values: Record<string, string>) { const next = new URLSearchParams(search); for (const [key, value] of Object.entries(values)) next.set(key, value); navigate(`${location}?${next}`); }
  return <section aria-busy={loaded !== query && !error}><h2 className="mb-5 text-xl font-semibold">{t("audit")}</h2>
    <form className="mb-5 flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); const value = new FormData(event.currentTarget).get("q"); filter({ auditQ: typeof value === "string" ? value.trim() : "", auditOffset: "0" }); }}><input key={params.get("auditQ")} name="q" defaultValue={params.get("auditQ") ?? ""} aria-label={t("auditSearch")} placeholder={t("auditSearch")} maxLength={255} className={modelField + " sm:max-w-80"} /><button className={modelButton}>{t("searchAction")}</button><select className={modelField.replace("w-full", "w-auto")} aria-label={t("updated")} value={params.get("auditDays") ?? "0"} onChange={event => filter({ auditDays: event.target.value, auditOffset: "0" })}>{[[0, "allTime"], [7, "week"], [30, "month"]].map(([value, key]) => <option key={value} value={value}>{t(String(key))}</option>)}</select></form>
    {error ? <div role="alert" className="rounded-xl border border-amber-300/25 p-5 text-sm text-amber-200"><p>{t(errorKey(error))}</p><button className={modelButton + " mt-3"} onClick={() => setReload(value => value + 1)}>{t("refresh")}</button></div> : loaded !== query ? <p role="status" className="p-8 text-sm text-slate-500">{t("loading")}</p> : <>
      <ul className="divide-y divide-white/10 rounded-2xl border border-white/10 bg-[#11141D] px-5">{page?.items.map(item => <li key={item.id} className="py-5"><p className="text-sm text-orange-200">{t(({ upsert_model: "actionModel", publish_pricing: "actionPrice", set_model_active: "actionActive", upsert_policy: "actionPolicy" })[item.action] ?? item.action)}</p><p className="mt-2 break-all font-mono text-xs text-slate-400">{item.objectId}</p><p className="mt-2 text-xs leading-6 text-slate-500">{t("actor")} {item.actorId} · {new Date(item.createdAt).toLocaleString(i18n.resolvedLanguage)}</p><p className="mt-2 text-sm text-slate-300">{item.reason}</p><p className="mt-2 break-all font-mono text-[10px] text-slate-600">{t("request")} {item.requestId}</p><details className="mt-4 text-xs text-slate-500"><summary className="cursor-pointer">{t("before")} / {t("after")}</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/30 p-3">{JSON.stringify({ [t("before")]: item.before, [t("after")]: item.after }, null, 2)}</pre></details></li>)}</ul>
      {!page?.items.length && <p className="py-12 text-sm text-slate-500">{t("emptyAudit")}</p>}{page && <div className="mt-5 flex flex-wrap justify-between gap-3 text-xs text-slate-500"><span>{t("page", { total: page.total, page: Math.floor(page.offset / page.limit) + 1 })}</span><div className="flex gap-2"><button className={modelButton} disabled={page.offset === 0} onClick={() => filter({ auditOffset: String(Math.max(0, page.offset - page.limit)) })}>{t("previous")}</button><button className={modelButton} disabled={page.offset + page.limit >= page.total} onClick={() => filter({ auditOffset: String(page.offset + page.limit) })}>{t("next")}</button></div></div>}
    </>}
  </section>;
}
