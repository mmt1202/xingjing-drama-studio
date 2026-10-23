import { useEffect, useState } from "react";
import { Link, useLocation } from "wouter";
import { House } from "lucide-react";
import { authenticatedFetch } from "@/utils/auth";
import type { SessionContext } from "@/xingjing_identity/api";
import { homeButton, useHomeTranslation } from "./i18n";

export function CreatorHomeLink() {
  const { t } = useHomeTranslation();
  return <Link href="/app/home" className={homeButton}><House size={15} />{t("home")}</Link>;
}
export function CreatorHomeEntry() {
  const { t } = useHomeTranslation(); const [, navigate] = useLocation();
  const [failed, setFailed] = useState(false); const [reload, setReload] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void authenticatedFetch("/api/v1/session/context", { signal: abort.signal }).then(async (response) => {
      if (!response.ok) throw new Error("SESSION_UNAVAILABLE");
      const body = await response.json() as { data: SessionContext };
      if (!abort.signal.aborted) navigate(body.data.currentWorkspace ? `/app/workspaces/${encodeURIComponent(body.data.currentWorkspace.id)}/home` : "/workspace-select", { replace: true });
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); });
    return () => abort.abort();
  }, [navigate, reload]);
  return <main className="grid min-h-screen place-content-center gap-4 bg-[#05060A] p-6 text-slate-200"><p role={failed ? "alert" : "status"}>{t(failed ? "failed" : "loading")}</p>{failed && <button className={homeButton} onClick={() => { setFailed(false); setReload((value) => value + 1); }}>{t("retry")}</button>}</main>;
}
