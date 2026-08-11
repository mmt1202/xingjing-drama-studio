import { useCallback, useEffect, useState } from "react";

import { PostproductionApiError, type PostproductionApi } from "./api";
import type { ComplianceRecord, ExportTask, ProjectScope, Timeline } from "./types";

export interface PostproductionCopy {
  title: string; loading: string; denied: string; retryPermission: string; retry: string; emptyTimeline: string;
  emptyCompliance: string; emptyExports: string; render: string; exporting: string; createExport: string; appeal: string;
  conflict: string; reloadLatest: string; failed: string; blocked: string; passed: string; processing: string;
  timeline: string; versions: string; compliance: string; rights: string; exports: string; releasePackages: string;
  aigc: string; tracks: string; version: string; risk: string; owner: string; unblock: string; status: string;
}

// 独立资源可由主线 i18n 适配器替换；组件本身不依赖全局命名空间。
export const zhCNPostproductionCopy: PostproductionCopy = {
  title: "成片与合规交付", loading: "正在恢复工作区与项目上下文…", denied: "没有访问权限", retryPermission: "重新校验权限",
  retry: "重试", emptyTimeline: "尚无时间线，请先完成媒体与音频生产。", emptyCompliance: "尚无合规记录，可发起内容检查。",
  emptyExports: "尚无导出或发布包。", render: "提交最终合成", exporting: "正在创建导出…", createExport: "创建正式导出",
  appeal: "提交申诉", conflict: "版本冲突：服务端已有更新，未覆盖现有数据。", reloadLatest: "读取服务端最新版本", failed: "请求失败",
  blocked: "已阻断", passed: "已通过", processing: "处理中", timeline: "时间轴", versions: "版本记录与对比", compliance: "内容审核与风险",
  rights: "授权证明", exports: "导出任务", releasePackages: "发布包", aigc: "水印 / AIGC 标识", tracks: "轨道", version: "版本",
  risk: "风险", owner: "责任人", unblock: "解除条件", status: "状态",
};

type LoadState = "loading" | "ready" | "denied" | "failed";

export interface PostproductionWorkspaceProps {
  api: PostproductionApi;
  scope: ProjectScope;
  copy: PostproductionCopy;
  exportPreset?: { target: string; format: string };
}

export function PostproductionWorkspace({ api, scope, copy, exportPreset }: PostproductionWorkspaceProps) {
  const [state, setState] = useState<LoadState>("loading");
  const [timelines, setTimelines] = useState<Timeline[]>([]);
  const [compliance, setCompliance] = useState<ComplianceRecord[]>([]);
  const [exports, setExports] = useState<ExportTask[]>([]);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<unknown>();

  const refresh = useCallback(async () => {
    setState("loading"); setError(undefined);
    try {
      const context = await api.getSessionContext(scope.workspaceId);
      if (context.workspaceId !== scope.workspaceId) throw new PostproductionApiError(403, "WORKSPACE_MISMATCH", "workspace mismatch");
      setPermissions(context.permissions);
      const [timelinePage, compliancePage, exportPage] = await Promise.all([
        context.permissions.includes("final.view") ? api.listTimelines(scope) : Promise.resolve({ items: [], total: 0 }),
        context.permissions.includes("compliance.view") ? api.listCompliance(scope) : Promise.resolve({ items: [], total: 0 }),
        context.permissions.includes("export.view") ? api.listExports(scope) : Promise.resolve({ items: [], total: 0 }),
      ]);
      setTimelines(timelinePage.items); setCompliance(compliancePage.items); setExports(exportPage.items); setState("ready");
    } catch (cause) {
      setError(cause); setState((cause as { status?: number }).status === 403 ? "denied" : "failed");
    }
  }, [api, scope]);

  useEffect(() => { void Promise.resolve().then(refresh); }, [refresh]);

  const reloadTimelines = async () => { const page = await api.listTimelines(scope); setTimelines(page.items); setError(undefined); };
  const act = async (key: string, action: () => Promise<unknown>, reload: () => Promise<void>) => {
    if (busy) return;
    setBusy(key); setError(undefined);
    try { await action(); await reload(); } catch (cause) { setError(cause); } finally { setBusy(undefined); }
  };
  const currentCompliance = compliance[0];
  const canExport = permissions.includes("export.manage") && currentCompliance?.status === "passed" && exportPreset;
  const conflict = (error as { status?: number; code?: string } | undefined)?.status === 409;
  const renderError = Boolean(error) && state === "ready";
  const statusText: Record<string, string> = { passed: copy.passed, blocked: copy.blocked, processing: copy.processing };

  if (state === "loading") return <main aria-busy="true"><p>{copy.loading}</p></main>;
  if (state === "denied") return <main><div role="alert">{copy.denied}</div><button type="button" onClick={() => void refresh()}>{copy.retryPermission}</button></main>;
  if (state === "failed") return <main><div role="alert">{copy.failed}</div><button type="button" onClick={() => void refresh()}>{copy.retry}</button></main>;

  return <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
    <header className="mb-6"><p className="text-xs text-cyan-300">{scope.workspaceId} / {scope.projectId}{scope.episodeId ? ` / ${scope.episodeId}` : ""}</p><h1 className="text-2xl font-semibold">{copy.title}</h1></header>
    {renderError && <div role="alert" className="mb-4 rounded border border-amber-500 p-3">{conflict ? copy.conflict : copy.failed}{conflict && <button type="button" className="ml-3 underline" onClick={() => void reloadTimelines()}>{copy.reloadLatest}</button>}</div>}
    <div className="grid gap-5 xl:grid-cols-2">
      <section aria-labelledby="timeline-title" className="rounded-xl border border-slate-700 bg-slate-900 p-4">
        <h2 id="timeline-title" className="font-medium">{copy.timeline}</h2>
        {timelines.length === 0 ? <p>{copy.emptyTimeline}</p> : timelines.map((timeline) => <article key={timeline.id} className="mt-3 rounded bg-slate-800 p-3">
          <div className="flex justify-between"><strong>{timeline.id}</strong><span>{copy.version} {timeline.version}</span></div>
          <p>{copy.tracks}: {timeline.tracks.map((track) => `${track.kind}(${track.clips.length})`).join(" · ") || "—"}</p>
          <h3 className="mt-2 text-sm text-slate-300">{copy.versions}</h3>
          <ul>{timeline.finalVersions.map((item) => <li key={item.id}>{item.label} · {statusText[item.status] ?? item.status} · v{item.version}</li>)}</ul>
          {permissions.includes("final.manage") && <button type="button" disabled={Boolean(busy)} onClick={() => void act("render", () => api.createRenderTask(scope, { timelineId: timeline.id, version: timeline.version }), reloadTimelines)}>{copy.render}</button>}
        </article>)}
      </section>
      <section aria-labelledby="compliance-title" className="rounded-xl border border-slate-700 bg-slate-900 p-4">
        <h2 id="compliance-title">{copy.compliance}</h2>
        {compliance.length === 0 ? <p>{copy.emptyCompliance}</p> : compliance.map((record) => <article key={record.id} className="mt-3 rounded bg-slate-800 p-3">
          <p>{copy.status}: {statusText[record.status] ?? record.status} · {copy.risk}: {record.riskLevel} · {copy.version}: {record.ruleVersion}</p>
          {record.owner && <p>{copy.owner}: {record.owner}</p>}{record.unblockCondition && <p>{copy.unblock}: {record.unblockCondition}</p>}
          <h3>{copy.rights}</h3><ul>{record.evidence.map((item) => <li key={item.id}>{item.type}: {item.name}</li>)}</ul>
          {record.status === "blocked" && permissions.includes("compliance.manage") && <button type="button" disabled={Boolean(busy)} onClick={() => void act("appeal", () => api.performComplianceAction(scope, { action: "appeal", recordId: record.id, version: record.version }), async () => { const page = await api.listCompliance(scope); setCompliance(page.items); })}>{copy.appeal}</button>}
        </article>)}
      </section>
      <section aria-labelledby="export-title" className="rounded-xl border border-slate-700 bg-slate-900 p-4 xl:col-span-2">
        <div className="flex justify-between"><h2 id="export-title">{copy.exports} / {copy.releasePackages}</h2>{canExport && <button type="button" disabled={Boolean(busy)} onClick={() => void act("export", () => api.createExport(scope, { projectVersion: currentCompliance.projectVersion, target: exportPreset.target, format: exportPreset.format }), async () => { const page = await api.listExports(scope); setExports(page.items); })}>{busy === "export" ? copy.exporting : copy.createExport}</button>}</div>
        {exports.length === 0 ? <p>{copy.emptyExports}</p> : <ul>{exports.map((item) => <li key={item.id}>{item.id} · {item.target} · {item.format} · {statusText[item.status] ?? item.status}{item.downloadUrl && item.complianceStatus === "passed" && <a className="ml-2 underline" href={item.downloadUrl}>下载 {item.id}</a>}</li>)}</ul>}
      </section>
    </div>
  </main>;
}
