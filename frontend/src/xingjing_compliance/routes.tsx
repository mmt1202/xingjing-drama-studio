import { useEffect, useMemo, useState, type ComponentType } from "react";

import { createComplianceExportApi } from "./api";
import { ComplianceExportPanel, type ComplianceExportPanelProps } from "./ComplianceExportPanel";
import type { ComplianceExportPort, ComplianceScope, ExportContextOption } from "./contracts";

export interface ComplianceRouteProps {
  readonly pageId?: string;
  readonly projectId: string;
  readonly projectVersion?: string;
  readonly target?: string;
  readonly api?: ComplianceExportPort;
}

export function ComplianceDeliveryPage({ pageId = "CR-018", projectId, projectVersion, target, api: suppliedApi }: ComplianceRouteProps) {
  const api = useMemo(() => suppliedApi ?? createComplianceExportApi(), [suppliedApi]);
  const scope = useMemo<ComplianceScope | null>(() => {
    const version = projectVersion?.trim();
    const exportTarget = target?.trim();
    return version && exportTarget ? { projectId, projectVersion: version, target: exportTarget } : null;
  }, [projectId, projectVersion, target]);
  if (!scope) return <ComplianceContextPicker api={api} projectId={projectId} pageId={pageId} />;
  const props: ComplianceExportPanelProps = { api, scope, pageId };
  return <main className="mx-auto max-w-6xl p-6"><ComplianceExportPanel {...props} /></main>;
}

function ComplianceContextPicker({ api, projectId, pageId }: {
  readonly api: ComplianceExportPort;
  readonly projectId: string;
  readonly pageId: string;
}) {
  const [contexts, setContexts] = useState<readonly ExportContextOption[]>([]);
  const [projectVersion, setProjectVersion] = useState("");
  const [target, setTarget] = useState("");
  const [activeScope, setActiveScope] = useState<ComplianceScope>();
  const [state, setState] = useState<"loading" | "ready" | "empty" | "failed">("loading");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api.listExportContexts(projectId, controller.signal).then((items) => {
      setContexts(items);
      setProjectVersion(items[0]?.projectVersion ?? "");
      setState(items.length ? "ready" : "empty");
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setMessage(error instanceof Error ? error.message : "读取成片版本失败");
        setState("failed");
      }
    });
    return () => controller.abort();
  }, [api, projectId]);
  if (activeScope) return <main className="mx-auto max-w-6xl p-6"><ComplianceExportPanel api={api} scope={activeScope} pageId={pageId} /></main>;
  return <main className="mx-auto max-w-6xl p-6">
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h1 className="text-lg font-semibold text-slate-900">选择正式交付上下文</h1>
      {state === "loading" ? <p className="mt-2 text-sm text-slate-600">正在读取项目的不可变成片版本…</p> : null}
      {state === "empty" ? <p className="mt-2 text-sm text-amber-800">当前项目还没有已完成的不可变成片版本，请先完成 M08 渲染。</p> : null}
      {state === "failed" ? <p role="alert" className="mt-2 text-sm text-red-700">{message}</p> : null}
      {state === "ready" ? <form className="mt-4 grid max-w-xl gap-4" onSubmit={(event) => {
        event.preventDefault();
        if (projectVersion && target.trim()) setActiveScope({ projectId, projectVersion, target: target.trim() });
      }}>
        <label className="text-sm font-medium text-slate-700">不可变成片版本
          <select value={projectVersion} onChange={(event) => setProjectVersion(event.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2">
            {contexts.map((item) => <option key={item.projectVersion} value={item.projectVersion}>{item.projectVersion} · {new Date(item.createdAt).toLocaleString()}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium text-slate-700">发布目标
          <input required maxLength={128} value={target} onChange={(event) => setTarget(event.target.value)} placeholder="例如 commercial、douyin、bilibili" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <button type="submit" disabled={!projectVersion || !target.trim()} className="w-fit rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:bg-slate-400">进入合规与正式交付</button>
      </form> : null}
    </section>
  </main>;
}

export interface ComplianceRouteExport {
  readonly pageId: string;
  readonly path: string;
  readonly component: ComponentType<ComplianceRouteProps>;
}

// 由主路由按产品路由策略接入；此模块只保留可发现的生产路由导出点。
export const complianceRouteExports: readonly ComplianceRouteExport[] = [
  { pageId: "CR-018", path: "/projects/:projectId/compliance", component: ComplianceDeliveryPage },
  { pageId: "CR-019", path: "/projects/:projectId/compliance/report", component: ComplianceDeliveryPage },
  { pageId: "CR-020", path: "/projects/:projectId/rights", component: ComplianceDeliveryPage },
  { pageId: "CR-031", path: "/projects/:projectId/exports", component: ComplianceDeliveryPage },
  { pageId: "CR-032", path: "/projects/:projectId/exports/configuration", component: ComplianceDeliveryPage },
  { pageId: "CR-033", path: "/projects/:projectId/exports/history", component: ComplianceDeliveryPage },
  { pageId: "CR-062", path: "/projects/:projectId/provenance", component: ComplianceDeliveryPage },
  { pageId: "CR-004", path: "/projects/:projectId/aigc-labeling", component: ComplianceDeliveryPage },
  { pageId: "CR-021", path: "/projects/:projectId/exports/cost-report", component: ComplianceDeliveryPage },
  { pageId: "CR-026", path: "/projects/:projectId/exports/davinci-edl", component: ComplianceDeliveryPage },
  { pageId: "CR-047", path: "/projects/:projectId/risks/likeness", component: ComplianceDeliveryPage },
  { pageId: "CR-054", path: "/projects/:projectId/rights/ip", component: ComplianceDeliveryPage },
  { pageId: "CR-055", path: "/projects/:projectId/exports/jianying", component: ComplianceDeliveryPage },
  { pageId: "CR-071", path: "/projects/:projectId/compliance/sensitive-words", component: ComplianceDeliveryPage },
  { pageId: "CR-072", path: "/projects/:projectId/exports/premiere", component: ComplianceDeliveryPage },
  { pageId: "CR-080", path: "/projects/:projectId/releases/materials", component: ComplianceDeliveryPage },
  { pageId: "CR-081", path: "/projects/:projectId/releases/package/:releaseId", component: ComplianceDeliveryPage },
  { pageId: "CR-082", path: "/projects/:projectId/releases/rules", component: ComplianceDeliveryPage },
  { pageId: "CR-083", path: "/projects/:projectId/releases", component: ComplianceDeliveryPage },
  { pageId: "CR-086", path: "/projects/:projectId/authorizations", component: ComplianceDeliveryPage },
  { pageId: "CR-087", path: "/projects/:projectId/compliance/appeal", component: ComplianceDeliveryPage },
];
