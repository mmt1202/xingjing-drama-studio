import { useEffect, useState, type ReactNode } from "react";

import { ApiError, type IdentityClient, type SessionContext } from "./api";
import { StatusPanel } from "./StatusPanel";

export function SessionGate({ client, children, onSessionExpired }: {
  client: IdentityClient; children: (context: SessionContext) => ReactNode; onSessionExpired?: () => void;
}) {
  const [state, setState] = useState<{ context?: SessionContext; error?: ApiError }>({});
  function load() {
    setState({});
    void client.getSessionContext().then((context) => setState({ context })).catch((reason) => setState({ error: reason instanceof ApiError ? reason : new ApiError({ status: 0, code: "UNKNOWN", message: "加载失败", retryable: true }) }));
  }
  useEffect(() => {
    let active = true;
    void client.getSessionContext()
      .then((context) => { if (active) setState({ context }); })
      .catch((reason) => { if (active) setState({ error: reason instanceof ApiError ? reason : new ApiError({ status: 0, code: "UNKNOWN", message: "加载失败", retryable: true }) }); });
    return () => { active = false; };
  }, [client]);

  if (state.context) return <>{children(state.context)}</>;
  if (!state.error) return <div role="status" className="animate-pulse rounded-2xl border border-white/10 p-6 text-white/60">正在加载工作区…</div>;
  if (state.error.status === 401) return <StatusPanel title="会话已过期" message="为了保护账号安全，请重新登录。" tone="warning" action={<button type="button" onClick={onSessionExpired} className="rounded-xl bg-violet-300 px-4 py-2 text-slate-950">重新登录</button>} />;
  if (state.error.status === 403) return <StatusPanel title="权限不足" message="你没有访问此内容的权限。如需访问，请联系工作区管理员。" tone="danger" />;
  return <StatusPanel title="加载失败" message={`${state.error.message}${state.error.requestId ? `（请求 ID：${state.error.requestId}）` : ""}`} action={<button type="button" onClick={load} className="rounded-xl border border-white/15 px-4 py-2 text-white">重试</button>} />;
}
