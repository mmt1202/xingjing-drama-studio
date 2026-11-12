import { useEffect, useMemo, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { createAuthenticatedFetcher, getTokenForSession } from "@/utils/auth";
import { useBudgetTranslation } from "./i18n";

export function BudgetUsagePanel({ projectId, revision }: { projectId: string; revision: string }) {
  const { t, i18n } = useBudgetTranslation();
  const epoch = useAuthStore((state) => state.sessionEpoch);
  const fetcher = useMemo(() => {
    const bound = createAuthenticatedFetcher();
    return (input: RequestInfo | URL, init?: RequestInit) => { getTokenForSession(epoch); return bound(input, init); };
  }, [epoch]);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ key: string; limit?: number | null; currency?: string; used?: number; unassigned?: boolean; failed?: boolean }>();
  const key = JSON.stringify([epoch, projectId, revision, retry]);
  const current = state?.key === key ? state : undefined;
  useEffect(() => {
    const abort = new AbortController();
    void fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/budget`, { signal: abort.signal, headers: { "Accept-Language": i18n.resolvedLanguage ?? "zh" } })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        const body: unknown = await response.json();
        if (!body || typeof body !== "object" || !("data" in body) || !body.data || typeof body.data !== "object") throw new Error();
        const data = body.data;
        if (!("projectLimitMinor" in data) || (data.projectLimitMinor !== null && (typeof data.projectLimitMinor !== "number" || !Number.isSafeInteger(data.projectLimitMinor) || data.projectLimitMinor < 0))
          || !("currency" in data) || typeof data.currency !== "string" || !("usage" in data) || !Array.isArray(data.usage)) throw new Error();
        const rows: unknown[] = data.usage;
        let used = 0;
        for (const row of rows) {
          if (!row || typeof row !== "object" || !("currency" in row) || !("consumed_minor" in row) || typeof row.consumed_minor !== "number"
            || !("reserved_minor" in row) || typeof row.reserved_minor !== "number"
            || !Number.isSafeInteger(row.consumed_minor) || row.consumed_minor < 0
            || !Number.isSafeInteger(row.reserved_minor) || row.reserved_minor < 0) throw new Error();
          if (row.currency === data.currency) used += row.consumed_minor + row.reserved_minor;
        }
        const unassigned = "unattributedUsage" in data && Array.isArray(data.unattributedUsage) && data.unattributedUsage.some((row: unknown) =>
          row && typeof row === "object" && (("consumed_minor" in row && typeof row.consumed_minor === "number" && row.consumed_minor > 0)
            || ("reserved_minor" in row && typeof row.reserved_minor === "number" && row.reserved_minor > 0)));
        if (!abort.signal.aborted) setState({ key, limit: data.projectLimitMinor, currency: data.currency, used, unassigned });
      }).catch(() => { if (!abort.signal.aborted) setState({ key, failed: true }); });
    return () => abort.abort();
  }, [fetcher, projectId, key, i18n.resolvedLanguage]);
  if (!current) return <p role="status" className="mt-4 text-xs text-slate-400">{t("loading")}</p>;
  if (current.failed) return <div role="status" className="mt-4 text-xs text-amber-200"><p>{t("readFailed")}</p><button className="mt-2 rounded border border-white/10 px-3 py-2" type="button" onClick={() => setRetry((count) => count + 1)}>{t("retry")}</button></div>;
  const remaining = current.limit == null ? null : Math.max(0, current.limit - (current.used ?? 0));
  return <div className="mt-4 space-y-2 text-xs leading-6 text-slate-400">
    <p>{t(current.limit == null ? "unlimited" : "active")}</p>
    {remaining !== null && current.currency ? <p>{t("remaining")}：{new Intl.NumberFormat(i18n.resolvedLanguage, { style: "currency", currency: current.currency }).format(remaining / 100)}</p> : null}
    {current.unassigned ? <p className="text-amber-200">{t("unassigned")}</p> : null}
  </div>;
}
