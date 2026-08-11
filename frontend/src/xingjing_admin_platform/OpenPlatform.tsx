import { useCallback, useEffect, useState } from "react";

import type { AdminApi, AdminContext, AdminRecord } from "./types";
import "./admin-platform.css";

function scalar(value: unknown, fallback: string): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
}

export function OpenPlatform({ api }: { api: AdminApi }) {
  const [context, setContext] = useState<AdminContext>();
  const [items, setItems] = useState<AdminRecord[]>([]);
  const [secret, setSecret] = useState<{ value: string; kind: "api_key" | "webhook_signing_secret" }>();
  const [error, setError] = useState<"denied"|"failed"|"conflict">();
  const [processing, setProcessing] = useState(false);
  const [selected, setSelected] = useState<AdminRecord>();
  const [name, setName] = useState("新 API 客户端");
  const [scopes, setScopes] = useState("projects:read");
  const [rateLimit, setRateLimit] = useState(120);
  const [rateWindowSeconds, setRateWindowSeconds] = useState(60);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookEvents, setWebhookEvents] = useState("task.completed");
  const load = useCallback(async () => {
    try {
      const next = await api.getContext();
      if (!next.permissions.includes("admin.api.view")) { setError("denied"); return; }
      setContext(next);
      setItems((await api.list("api", { page: 1, pageSize: 20, resource: "api-clients" })).items);
      setError(undefined);
    } catch { setError("failed"); }
  }, [api]);
  // The request-backed state machine intentionally starts when its injected API changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  if (error === "denied" || error === "failed") return <section className="xj-admin-state"><h1>{error === "denied" ? "无权访问此后台能力" : "开放平台服务暂时不可用"}</h1><button onClick={() => void load()}>重新读取</button></section>;

  const action = async (actionName: string, item?: AdminRecord, payload?: Record<string, unknown>) => {
    setProcessing(true);
    try {
      const result = await api.act("api", {
        action: actionName,
        objectId: item?.id ?? "new",
        version: item?.version ?? 0,
        resource: "api-clients",
        payload,
      });
      if (result.oneTimeSecret) setSecret({ value: result.oneTimeSecret, kind: result.oneTimeSecretKind ?? "api_key" });
      await load();
    } catch (cause) {
      setError(typeof cause === "object" && cause !== null && "status" in cause && cause.status === 409 ? "conflict" : "failed");
    } finally { setProcessing(false); }
  };
  const configure = async () => {
    const parsedScopes = scopes.split(",").map((value) => value.trim()).filter(Boolean);
    const parsedEvents = webhookEvents.split(",").map((value) => value.trim()).filter(Boolean);
    const payload: Record<string, unknown> = { name, scopes: parsedScopes, rateLimit, rateWindowSeconds };
    if (selected && webhookUrl.trim()) {
      const currentWebhook = selected.webhooks?.[0];
      payload.webhook = {
        ...(currentWebhook
          ? { id: currentWebhook.id, version: currentWebhook.version }
          : {}),
        url: webhookUrl.trim(),
        events: parsedEvents,
      };
    }
    await action(selected ? "configure_client" : "create_key", selected, payload);
    setSelected(undefined);
  };
  const select = (item: AdminRecord) => {
    setSelected(item);
    setName(item.name);
    setScopes(item.scopes?.join(", ") ?? "projects:read");
    const details = item.details ?? {};
    setRateLimit(typeof details.rateLimit === "number" ? details.rateLimit : 120);
    setRateWindowSeconds(typeof details.rateWindowSeconds === "number" ? details.rateWindowSeconds : 60);
    setWebhookUrl(item.webhooks?.[0]?.url ?? "");
  };
  return <main className="xj-admin-shell">
    <header className="xj-admin-header"><div><p className="xj-admin-kicker">AD-002 · OPEN PLATFORM</p><h1>开放 API</h1><p>{context?.workspace.name ?? "正在验证独立后台会话…"}</p></div><button onClick={() => void load()}>重新读取</button></header>
    {secret && <section className="xj-admin-secret" role="dialog" aria-label={secret.kind === "api_key" ? "一次性 API Key" : "一次性 Webhook 签名密钥"}><p>{secret.kind === "api_key" ? "API Key" : "Webhook 签名密钥"}仅展示一次。关闭后无法再次查看。</p><code>{secret.value}</code><button onClick={() => setSecret(undefined)}>我已安全保存</button></section>}
    {error === "conflict" && <section className="xj-admin-notice is-conflict" role="status"><strong>数据已被其他管理员更新</strong><button onClick={() => { setError(undefined); void load(); }}>读取最新版本</button></section>}
    {context?.permissions.includes("admin.api.manage") && <section className="xj-admin-toolbar" aria-label="开放平台配置">
      <input aria-label="客户端名称" value={name} onChange={(event) => setName(event.target.value)} />
      <input aria-label="权限范围" value={scopes} onChange={(event) => setScopes(event.target.value)} placeholder="projects:read, tasks:read" />
      <input aria-label="限流次数" type="number" min={1} value={rateLimit} onChange={(event) => setRateLimit(Number(event.target.value))} />
      <input aria-label="限流窗口秒数" type="number" min={1} value={rateWindowSeconds} onChange={(event) => setRateWindowSeconds(Number(event.target.value))} />
      <input disabled={!selected} aria-label="Webhook HTTPS 地址" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder={selected ? "https://example.com/webhooks/xingjing" : "创建客户端后可配置 Webhook"} />
      <input disabled={!selected} aria-label="Webhook 事件" value={webhookEvents} onChange={(event) => setWebhookEvents(event.target.value)} placeholder="task.completed, export.completed" />
      <button disabled={processing || !name.trim()} onClick={() => void configure()}>{selected ? "保存客户端配置" : "创建客户端和 API Key"}</button>
      {selected && <button onClick={() => setSelected(undefined)}>取消编辑</button>}
    </section>}
    {!items.length ? <section className="xj-admin-empty"><h2>当前工作区还没有 API 客户端</h2><p>具备管理权限时，可以在上方创建真实客户端和只展示一次的密钥。</p></section> : <section className="xj-admin-table-wrap"><table><thead><tr><th>客户端</th><th>密钥</th><th>Scope</th><th>Webhook</th><th>调用与限流</th><th>状态</th><th>操作</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}>
      <td><strong>{item.name}</strong><small>{item.id} · v{item.version}</small></td><td><code>{item.maskedSecret}</code></td><td>{item.scopes?.join(", ") || "—"}</td><td>{item.webhooks?.length ? item.webhooks.map((webhook) => <div key={webhook.id}><span>{webhook.url}</span> · {webhook.status} · v{webhook.version}</div>) : "—"}</td>
      <td>{scalar(item.details?.usageCount, "0")} 次 · 最近 {scalar(item.details?.lastOutcome, "无调用")}<small>{scalar(item.details?.rateLimit, "—")} / {scalar(item.details?.rateWindowSeconds, "—")} 秒</small></td><td>{item.status}</td>
      <td><div className="xj-admin-actions"><button disabled={processing} onClick={() => select(item)}>配置</button><button disabled={processing || item.status !== "active"} aria-label={`轮换${item.name}`} onClick={() => void action("rotate_key", item)}>轮换</button><button disabled={processing || item.status !== "active"} aria-label={`撤销${item.name}`} onClick={() => void action("revoke_key", item)}>撤销</button></div></td>
    </tr>)}</tbody></table></section>
    }
  </main>;
}
