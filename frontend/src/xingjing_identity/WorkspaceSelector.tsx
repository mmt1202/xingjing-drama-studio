import { useState } from "react";

import { ApiError, type IdentityClient, type SessionContext } from "./api";
import { StatusPanel } from "./StatusPanel";

export function WorkspaceSelector({ context, client, onSelected }: {
  context: SessionContext; client: IdentityClient; onSelected?: (context: SessionContext) => void;
}) {
  const [selecting, setSelecting] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  if (context.workspaces.length === 0) return <StatusPanel title="暂无可访问的工作区" message="账号当前没有有效的工作区成员关系，请联系管理员或稍后重试。" />;

  async function select(id: string) {
    if (selecting) return;
    setSelecting(id); setError(null);
    try { onSelected?.(await client.selectWorkspace(id)); }
    catch (reason) { setError(reason instanceof ApiError ? reason : new ApiError({ status: 0, code: "UNKNOWN", message: "切换失败", retryable: true })); }
    finally { setSelecting(null); }
  }

  return <section aria-labelledby="workspace-heading" className="mx-auto max-w-3xl">
    <header><p className="text-sm text-violet-200/70">欢迎回来，{context.user.displayName}</p><h1 id="workspace-heading" className="mt-2 text-3xl font-semibold text-white">选择工作空间</h1><p className="mt-2 text-white/55">仅显示服务端确认你有权访问的工作区</p></header>
    {error && <div role="alert" className="mt-5 rounded-xl border border-red-400/25 bg-red-400/10 p-4 text-red-100">{error.status === 403 ? "你无权进入该工作区" : error.message}{error.requestId ? `（请求 ID：${error.requestId}）` : ""}</div>}
    <ul className="mt-7 grid gap-4 sm:grid-cols-2">
      {context.workspaces.map((workspace) => <li key={workspace.id} aria-label={workspace.name} className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <div className="flex items-start justify-between gap-4"><div><h2 className="font-medium text-white">{workspace.name}</h2><p className="mt-1 text-xs text-white/40">{workspace.slug ?? workspace.id}</p></div><span className="rounded-full border border-violet-300/20 bg-violet-300/10 px-2 py-1 text-xs text-violet-200">{workspace.role}</span></div>
        <div className="mt-5 flex items-center justify-between"><span className="text-xs text-emerald-300/75">{workspace.status ?? "可用"}</span><button type="button" disabled={selecting !== null} onClick={() => void select(workspace.id)} className="rounded-lg bg-white/10 px-3 py-2 text-sm text-white disabled:opacity-50">{selecting === workspace.id ? "正在进入…" : `进入${workspace.name}`}</button></div>
      </li>)}
    </ul>
  </section>;
}
