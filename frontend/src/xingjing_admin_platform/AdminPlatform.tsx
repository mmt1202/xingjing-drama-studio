import { useCallback, useEffect, useState } from "react";

import { adminRouteMap } from "./routes";
import type { AdminApi, AdminContext, AdminRecord } from "./types";
import "./admin-platform.css";

function statusOf(error: unknown): number | undefined { return typeof error === "object" && error !== null && "status" in error ? Number(error.status) : undefined }
function mask(value: string): string {
  if (/^1\d{10}$/.test(value)) return `${value.slice(0, 3)}****${value.slice(-4)}`;
  if (value.includes("@")) { const [name, domain] = value.split("@"); return `${name?.slice(0, 1) ?? "*"}***@${domain ?? "***"}`; }
  return value.length > 6 ? `${value.slice(0, 2)}••••${value.slice(-2)}` : "••••";
}
function detailText(details: AdminRecord["details"]): string {
  if (!details) return "—";
  return Object.entries(details).map(([key, value]) => {
    const rendered = Array.isArray(value)
      ? value.map((item) => typeof item === "string" || typeof item === "number" ? String(item) : JSON.stringify(item)).join("、")
      : typeof value === "object" && value !== null
        ? JSON.stringify(value)
        : typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : "—";
    return `${key}: ${rendered}`;
  }).join(" · ");
}

export function AdminPlatform({ routeId, api }: { routeId: string; api: AdminApi }) {
  const route = adminRouteMap.get(routeId);
  const [context, setContext] = useState<AdminContext>();
  const [items, setItems] = useState<AdminRecord[]>();
  const [state, setState] = useState<"loading"|"ready"|"denied"|"failed">("loading");
  const [actionState, setActionState] = useState<"idle"|"processing"|"success"|"conflict"|"failed">("idle");
  const [draftObjectId, setDraftObjectId] = useState("");
  const [draftReason, setDraftReason] = useState("");
  const [draftPayload, setDraftPayload] = useState("{}");
  const [draftVersion, setDraftVersion] = useState(0);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    if (!route) return;
    setState("loading");
    try {
      const nextContext = await api.getContext();
      if (!nextContext.permissions.includes(route.viewPermission)) { setState("denied"); return; }
      setContext(nextContext);
      const page = await api.list(route.domain, { page: 1, pageSize: 20, resource: route.resource, projectId: nextContext.project?.id });
      setItems(page.items);
      setState("ready");
    } catch (error) { setState(statusOf(error) === 403 ? "denied" : "failed"); }
  }, [api, route]);
  // The request-backed state machine intentionally starts when its injected API changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);

  if (!route) return <section className="xj-admin-state"><h1>后台路由不存在</h1></section>;
  if (state === "loading") return <section className="xj-admin-state" aria-busy="true"><span className="xj-admin-pulse" /><p>正在验证后台会话与数据范围…</p></section>;
  if (state === "denied") return <section className="xj-admin-state xj-admin-denied"><p className="xj-admin-kicker">403 · ACCESS DENIED</p><h1>无权访问此后台能力</h1><p>权限拒绝不会展示任何业务对象或敏感字段。请联系后台安全管理员核对角色与数据范围。</p></section>;
  if (state === "failed") return <section className="xj-admin-state"><h1>后台服务暂时不可用</h1><p>未对写操作做自动重放。恢复后请重新读取服务端最终状态。</p><button onClick={() => void load()}>重新读取</button></section>;

  const canManage = context?.permissions.includes(route.managePermission) ?? false;
  const visibleItems = (items ?? []).filter((item) => {
    const needle = query.trim().toLocaleLowerCase();
    return !needle || [item.id, item.name, item.status, detailText(item.details)]
      .some((value) => value.toLocaleLowerCase().includes(needle));
  });
  const runAction = async (item: AdminRecord) => {
    setActionState("processing");
    try {
      await api.act(route.domain, {
        action: route.primaryAction,
        objectId: item.id,
        version: item.version,
        resource: route.resource,
      });
      setActionState("success"); await load();
    } catch (error) { setActionState(statusOf(error) === 409 ? "conflict" : "failed"); }
  };
  const runPageAction = async () => {
    if (!draftObjectId.trim()) return;
    setActionState("processing");
    try {
      const parsed = JSON.parse(draftPayload) as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("payload");
      await api.act(route.domain, {
        action: route.primaryAction,
        objectId: draftObjectId.trim(),
        version: draftVersion,
        resource: route.resource,
        reason: draftReason.trim() || undefined,
        payload: parsed as Record<string, unknown>,
      });
      setActionState("success");
      await load();
    } catch (error) {
      setActionState(statusOf(error) === 409 ? "conflict" : "failed");
    }
  };

  return <main className="xj-admin-shell">
    <header className="xj-admin-header">
      <div><p className="xj-admin-kicker">{route.id} · {route.domain.toUpperCase()}</p><h1>{route.title}</h1><p>{context?.workspace.name} · {context?.dataScope}</p></div>
      <div className="xj-admin-identity"><span>{context?.actor.displayName}</span><small>{context?.actor.role}</small></div>
    </header>
    <section className="xj-admin-toolbar" aria-label="查询条件"><input aria-label="搜索对象" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按对象 ID、名称或状态搜索" /><button onClick={() => void load()}>重新读取</button></section>
    {canManage && <section className="xj-admin-toolbar" aria-label={`${route.primaryAction}提交区`}>
      <input aria-label="业务对象 ID" value={draftObjectId} onChange={(event) => setDraftObjectId(event.target.value)} placeholder="业务对象 ID（新建时自定义稳定 ID）" />
      <input aria-label="预期版本" type="number" min={0} value={draftVersion} onChange={(event) => setDraftVersion(Number(event.target.value))} />
      <input aria-label="操作原因" value={draftReason} onChange={(event) => setDraftReason(event.target.value)} placeholder="操作原因或审批说明" />
      <textarea aria-label="结构化配置" value={draftPayload} onChange={(event) => setDraftPayload(event.target.value)} placeholder='JSON 配置，例如 {"name":"平台公告"}' />
      <button disabled={!draftObjectId.trim() || actionState === "processing"} onClick={() => void runPageAction()}>{route.primaryAction}</button>
    </section>}
    {actionState !== "idle" && <section className={`xj-admin-notice is-${actionState}`} role="status">
      {actionState === "processing" && `正在提交${route.primaryAction.replace("执行", "")}…`}
      {actionState === "success" && `已${route.primaryAction}，数据已重新读取`}
      {actionState === "failed" && "操作未完成，服务端未确认写入。请重新读取后再试。"}
      {actionState === "conflict" && <><strong>数据已被其他管理员更新</strong><button onClick={() => { setActionState("idle"); void load(); }}>读取最新版本</button></>}
    </section>}
    {!visibleItems.length ? <section className="xj-admin-empty"><h2>{items?.length ? "没有符合筛选条件的对象" : `当前数据范围内没有${route.emptyLabel}`}</h2><p>{items?.length ? "清除搜索词后可恢复完整列表。" : "可以调整服务端筛选条件或稍后重新读取，不展示演示数据。"}</p></section> :
      <section className="xj-admin-table-wrap"><table><thead><tr><th>对象</th><th>状态</th><th>业务明细</th><th>敏感信息</th><th>版本</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{visibleItems.map((item) => <tr key={item.id}>
        <td><strong>{item.name}</strong><small>{item.id}</small></td><td><span className={`xj-admin-status is-${item.status}`}>{item.status}</span></td>
        <td>{detailText(item.details)}</td><td>{Object.values(item.sensitive ?? {}).map(mask).join(" · ") || "—"}</td><td>v{item.version}</td><td>{item.updatedAt}</td>
        <td><button disabled={!canManage || actionState === "processing"} onClick={() => void runAction(item)}>{route.primaryAction}</button></td>
      </tr>)}</tbody></table></section>}
  </main>;
}
