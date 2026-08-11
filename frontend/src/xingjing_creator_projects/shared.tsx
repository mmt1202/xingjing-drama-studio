import { useCallback, useEffect, useState, type ReactNode } from "react";
import { CreatorApiError } from "./api";
import type { CreatorProjectsPort, Permission, SessionContext } from "./types";

export function CreatorPage({ api, permission, title, children }: { api: CreatorProjectsPort; permission: Permission; title: string; children: (context: SessionContext) => ReactNode }) {
  const [context, setContext] = useState<SessionContext>(); const [error, setError] = useState<Error>();
  const load = useCallback(() => { setError(undefined); void api.getSessionContext().then(setContext).catch((e: Error) => setError(e)); }, [api]);
  useEffect(() => { void api.getSessionContext().then(setContext).catch((e: Error) => setError(e)); }, [api]);
  if (error) return <StatePanel title="工作区恢复失败" detail={error.message} action="重试" onAction={load} />;
  if (!context) return <main className="min-h-64 bg-[#07080C] p-8 text-[#B8BECC]" aria-busy="true"><p>正在恢复工作区…</p></main>;
  if (!context.permissions.includes(permission)) return <StatePanel title={`没有${permission.startsWith("project") ? "项目" : "剧本"}${permission.endsWith("view") ? "查看" : "管理"}权限`} detail="请联系工作区管理员申请所需角色。" />;
  return <main className="min-h-full bg-[#07080C] p-6 text-[#F5F6FA]"><header className="mb-6 border-b border-[#2A2E3A] pb-4"><p className="text-xs text-[#7E8494]">{context.workspace.name}</p><h1 className="text-2xl font-bold">{title}</h1></header>{children(context)}</main>;
}

export function StatePanel({ title, detail, action, onAction }: { title: string; detail?: string; action?: string; onAction?: () => void }) { return <section className="m-6 rounded-2xl border border-[#2A2E3A] bg-[#12141B] p-8 text-[#F5F6FA]" role="status"><h2 className="text-lg font-semibold">{title}</h2>{detail && <p className="mt-2 text-sm text-[#B8BECC]">{detail}</p>}{action && <button className="mt-4 rounded-lg bg-[#FF6B4A] px-4 py-2" onClick={onAction}>{action}</button>}</section>; }

export function ActionError({ error, onRetry, onRefresh }: { error: Error; onRetry: () => void; onRefresh?: () => void }) {
  const apiError = error instanceof CreatorApiError ? error : undefined;
  if (apiError?.status === 403) return <StatePanel title="没有执行此操作的权限" detail={`服务端拒绝了请求（${apiError.code}）。请刷新权限或联系工作区管理员。${apiError.requestId ? ` 请求 ID：${apiError.requestId}` : ""}`} action={onRefresh ? "刷新" : undefined} onAction={onRefresh} />;
  if (apiError?.status === 409) return <StatePanel title="剧本已被其他人更新" detail={`为避免覆盖他人的修改，当前操作未写入（${apiError.code}）。请重新读取服务端版本后再决定。${apiError.requestId ? ` 请求 ID：${apiError.requestId}` : ""}`} action={onRefresh ? "重新读取" : "重试"} onAction={onRefresh ?? onRetry} />;
  return <StatePanel title={error.message} detail={apiError?.requestId ? `请求 ID：${apiError.requestId}` : "已保留已保存数据，可安全重试。"} action="重试" onAction={onRetry} />;
}
