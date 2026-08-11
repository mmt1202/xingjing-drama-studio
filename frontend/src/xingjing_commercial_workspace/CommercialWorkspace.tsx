import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Banknote, BriefcaseBusiness, CircleAlert, Gavel, PackageCheck, RefreshCw } from "lucide-react";

import { CommercialApiError, createCommercialClient, type CommercialOrder, type VersionedCommercialOrder } from "@/xingjing_commercial_client";

type CommercialPageId = "AD-005" | "AD-006" | "AD-007" | "AD-008" | "CR-012" | "CR-013" | "CR-014" | "CR-015" | "CR-016";
type CommandKind = "quote" | "contract" | "delivery" | "accept" | "return" | "dispute" | "resolve" | "freeze" | "resume" | "pay";

const pageTitles: Record<CommercialPageId, string> = {
  "AD-005": "商单详情管理", "AD-006": "商单争议处理", "AD-007": "商单结算管理", "AD-008": "平台商单管理",
  "CR-012": "商单验收", "CR-013": "商单交付", "CR-014": "商单详情", "CR-015": "商单中心", "CR-016": "商单结算",
};

function identifier(): string { return crypto.randomUUID(); }
function money(value: number, currency: string): string { return new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(value / 100); }

function errorText(reason: unknown): string {
  if (!(reason instanceof CommercialApiError)) return "请求未完成，请检查网络后重试。";
  if (reason.status === 503) return "商单服务尚未部署或暂时不可用。";
  if (reason.status === 403) return "你没有处理该商单的权限。";
  if (reason.status === 409 || reason.requiresRefresh) return "该商单已被其他参与方更新，已保留当前输入，请刷新后再提交。";
  if (reason.status === 502 && reason.code === "ACCOUNTING_REJECTED") return "账务通道尚未部署，本次资金动作没有生效。";
  return `操作未完成（${reason.code}）。`;
}

export function CommercialWorkspace({ pageId, workspaceId, accessToken }: { pageId: CommercialPageId; workspaceId: string; accessToken?: string }) {
  const admin = pageId.startsWith("AD-");
  const api = useMemo(() => accessToken ? createCommercialClient({ accessToken, workspaceId, surface: admin ? "admin" : "creator" }) : null, [accessToken, workspaceId, admin]);
  const [orders, setOrders] = useState<readonly CommercialOrder[]>([]);
  const [selected, setSelected] = useState<VersionedCommercialOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (selection = selected?.order.id) => {
    if (!api) { setLoading(false); setError("请先登录后再访问商单。 "); return; }
    setLoading(true); setError(null);
    try {
      const page = await api.listOrders({ limit: 50 });
      setOrders(page.items);
      const target = selection ?? page.items[0]?.id;
      setSelected(target ? await api.getOrder(target) : null);
    } catch (reason) { setError(errorText(reason)); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (!api) return;
    let active = true;
    void Promise.resolve().then(() => {
      if (active) { setLoading(true); setError(null); }
      return api.listOrders({ limit: 50 });
    })
      .then(async (page) => {
        const first = page.items[0];
        const detail = first ? await api.getOrder(first.id) : null;
        if (active) { setOrders(page.items); setSelected(detail); }
      })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);

  async function pick(orderId: string) {
    if (!api) return;
    setBusy(true); setError(null);
    try { setSelected(await api.getOrder(orderId)); } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); }
  }
  async function execute(kind: CommandKind, values: Record<string, string>) {
    if (!api || !selected) return;
    setBusy(true); setError(null);
    const orderId = selected.order.id;
    const command = { ifMatch: selected.etag, idempotencyKey: identifier(), requestId: identifier() };
    try {
      const next = kind === "quote" ? await api.submitQuote(orderId, { amount_minor: Number(values.amountMinor), currency: values.currency, proposal: values.proposal, valid_until: new Date(values.validUntil).toISOString() }, command)
        : kind === "contract" ? await api.recordContract(orderId, { content_ref: values.contentRef, content_digest: values.contentDigest, amount_minor: Number(values.amountMinor) }, command)
        : kind === "delivery" ? await api.submitDelivery(orderId, values.milestoneId, { artifact_version_id: values.artifactVersionId, artifact_digest: values.artifactDigest, note: values.note || null }, command)
        : kind === "accept" ? await api.acceptDelivery(orderId, values.deliveryId, { evidence_ref: values.evidenceRef }, command)
        : kind === "return" ? await api.returnDelivery(orderId, values.deliveryId, { reason: values.reason }, command)
        : kind === "dispute" ? await api.openDispute(orderId, values.milestoneId, { kind: values.kind, reason: values.reason }, command)
        : kind === "resolve" ? await api.resolveDispute(orderId, values.disputeId, { resolution: values.resolution }, command)
        : kind === "freeze" ? await api.freezeSettlement(orderId, values.settlementId, { amount_minor: Number(values.amountMinor), currency: values.currency }, command)
        : kind === "resume" ? await api.resumeSettlement(orderId, values.settlementId, { amount_minor: Number(values.amountMinor), currency: values.currency }, command)
        : await api.paySettlement(orderId, values.settlementId, { amount_minor: Number(values.amountMinor), currency: values.currency }, command);
      setSelected(next); await load(next.order.id);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  const visibleError = error ?? (!api ? "请先登录后再访问商单。" : null);
  return <main className="min-h-screen bg-[#0b1018] px-4 py-8 text-slate-100 sm:px-7 lg:px-10"><div className="mx-auto max-w-7xl"><header className="border-b border-white/10 pb-7"><p className="text-xs font-semibold tracking-[.18em] text-teal-200">星镜剧创 · {admin ? "平台商单" : "创作者商单"}</p><div className="mt-3 flex flex-wrap items-end justify-between gap-4"><h1 className="font-serif text-3xl tracking-tight text-white sm:text-4xl">{pageTitles[pageId]}</h1><button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-lg border border-white/15 px-3 py-2 text-sm text-slate-200"><RefreshCw size={15} />刷新服务端状态</button></div></header>{visibleError && <ErrorPanel text={visibleError} onRetry={() => void load()} />}{api && loading ? <Loading /> : <div className="mt-7 grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]"><OrderRail orders={orders} selectedId={selected?.order.id} busy={busy} onPick={(id) => void pick(id)} /><section>{selected ? <OrderSurface pageId={pageId} order={selected.order} busy={busy} onExecute={execute} /> : <EmptyOrders />}</section></div>}</div></main>;
}

function OrderRail({ orders, selectedId, busy, onPick }: { orders: readonly CommercialOrder[]; selectedId?: string; busy: boolean; onPick: (id: string) => void }) { return <aside className="rounded-2xl border border-white/10 bg-white/[.035] p-3"><p className="px-2 py-2 text-xs font-semibold tracking-[.16em] text-teal-200">商单列表</p>{orders.length === 0 ? <p className="px-2 py-5 text-sm text-slate-400">当前范围内没有商单。</p> : <ul className="space-y-1">{orders.map((order) => <li key={order.id}><button type="button" disabled={busy} onClick={() => onPick(order.id)} className={`w-full rounded-xl px-3 py-3 text-left transition ${order.id === selectedId ? "bg-teal-300 text-slate-950" : "text-slate-200 hover:bg-white/[.07]"}`}><span className="block truncate text-sm font-semibold">{order.title}</span><span className={`mt-1 block font-mono text-[11px] ${order.id === selectedId ? "text-slate-700" : "text-slate-500"}`}>{order.id} · {order.status}</span></button></li>)}</ul>}</aside>; }
function EmptyOrders() { return <section className="rounded-2xl border border-dashed border-white/15 p-9 text-center"><BriefcaseBusiness className="mx-auto text-slate-500" /><h2 className="mt-4 font-serif text-2xl">没有可展示的商单</h2><p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-400">列表只显示服务端按当前工作区和权限返回的数据，不会用演示订单填充页面。</p></section>; }
function Loading() { return <div role="status" className="mt-10 flex items-center gap-3 text-sm text-slate-300"><RefreshCw className="animate-spin text-teal-200" size={18} />正在读取商单数据…</div>; }
function ErrorPanel({ text, onRetry }: { text: string; onRetry: () => void }) { return <div role="alert" className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-300/30 bg-rose-950/40 px-4 py-3 text-sm text-rose-100"><span>{text}</span><button type="button" onClick={onRetry} className="rounded-lg border border-rose-200/30 px-3 py-1.5 text-xs">重试</button></div>; }

function OrderSurface({ pageId, order, busy, onExecute }: { pageId: CommercialPageId; order: CommercialOrder; busy: boolean; onExecute: (kind: CommandKind, values: Record<string, string>) => Promise<void> }) {
  const actions = actionsFor(pageId);
  return <div className="space-y-6"><section className="overflow-hidden rounded-2xl border border-white/10 bg-white/[.035]"><div className="grid gap-5 border-b border-white/10 p-5 md:grid-cols-[1fr_auto]"><div><p className="font-mono text-xs text-teal-200">{order.id}</p><h2 className="mt-2 font-serif text-2xl text-white">{order.title}</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">{order.requirements}</p></div><div className="text-left md:text-right"><p className="text-lg font-semibold text-white">{money(order.budget_minor, order.currency)}</p><p className="mt-1 text-sm text-teal-200">{order.status} · v{order.version}</p></div></div><div className="grid divide-y divide-white/10 md:grid-cols-3 md:divide-x md:divide-y-0"><Metric label="里程碑" value={String(order.milestones.length)} /><Metric label="交付版本" value={String(order.deliveries.length)} /><Metric label="争议" value={String(order.disputes.filter((item) => item.status === "open").length)} /></div></section><section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]"><div className="space-y-6"><Milestones order={order} /><EventSections order={order} /></div><aside className="space-y-4">{actions.length > 0 ? actions.map((kind) => <CommandCard key={kind} kind={kind} order={order} busy={busy} onExecute={onExecute} />) : <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5 text-sm leading-6 text-slate-400">此页面以查看服务端状态为主。选择交付、验收、争议或结算页面后，才会显示对应的受控动作。</section>}<section className="rounded-2xl border border-teal-300/20 bg-teal-300/[.05] p-5"><p className="text-xs font-semibold tracking-[.16em] text-teal-200">并发保护</p><p className="mt-2 text-sm leading-6 text-slate-300">每次提交自动携带服务端 ETag 和幂等键。版本冲突时不会覆盖他人的变更。</p></section></aside></section></div>;
}
function Metric({ label, value }: { label: string; value: string }) { return <div className="px-5 py-4"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold text-white">{value}</p></div>; }
function Milestones({ order }: { order: CommercialOrder }) { return <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5"><h3 className="font-serif text-xl text-white">履约里程碑</h3><ul className="mt-4 divide-y divide-white/10">{order.milestones.map((milestone) => <li key={milestone.id} className="grid gap-2 py-4 sm:grid-cols-[1fr_auto]"><div><p className="font-medium text-slate-100">{milestone.title}</p><p className="mt-1 text-sm text-slate-400">{milestone.acceptance_criteria}</p><p className="mt-2 font-mono text-xs text-slate-500">{milestone.id}</p></div><div className="text-left sm:text-right"><p className="text-sm text-teal-200">{milestone.status}</p><p className="mt-1 text-sm text-slate-300">{money(milestone.amount_minor, order.currency)}</p></div></li>)}</ul></section>; }
function EventSections({ order }: { order: CommercialOrder }) { return <section className="rounded-2xl border border-white/10 bg-white/[.035] p-5"><h3 className="font-serif text-xl text-white">交付、验收与结算</h3><div className="mt-4 grid gap-3 md:grid-cols-2">{order.deliveries.map((item) => <DataCard key={item.id} icon={<PackageCheck size={16} />} title={`交付 r${item.revision}`} meta={`${item.status} · ${item.id}`} />)}{order.disputes.map((item) => <DataCard key={item.id} icon={<Gavel size={16} />} title={`争议：${item.kind}`} meta={`${item.status} · ${item.reason}`} />)}{order.settlements.map((item) => <DataCard key={item.id} icon={<Banknote size={16} />} title={money(item.amount_minor, item.currency)} meta={`${item.status} · ${item.id}`} />)}{order.deliveries.length + order.disputes.length + order.settlements.length === 0 && <p className="text-sm text-slate-400">尚无交付、争议或结算记录。</p>}</div></section>; }
function DataCard({ icon, title, meta }: { icon: ReactNode; title: string; meta: string }) { return <div className="rounded-xl border border-white/10 bg-slate-950/50 p-3"><div className="flex items-center gap-2 text-teal-200">{icon}<span className="text-sm font-medium">{title}</span></div><p className="mt-2 break-all text-xs leading-5 text-slate-400">{meta}</p></div>; }

function actionsFor(pageId: CommercialPageId): CommandKind[] { if (pageId === "CR-014") return ["quote", "contract"]; if (pageId === "CR-013") return ["delivery"]; if (pageId === "CR-012") return ["accept", "return"]; if (pageId === "CR-016") return ["dispute"]; if (pageId === "AD-006") return ["resolve"]; if (pageId === "AD-007") return ["freeze", "resume", "pay"]; return []; }

function CommandCard({ kind, order, busy, onExecute }: { kind: CommandKind; order: CommercialOrder; busy: boolean; onExecute: (kind: CommandKind, values: Record<string, string>) => Promise<void> }) {
  const [values, setValues] = useState<Record<string, string>>(() => ({
    currency: order.currency,
    amountMinor: String(order.budget_minor),
    milestoneId: order.milestones[0]?.id ?? "",
    deliveryId: order.deliveries[0]?.id ?? "",
    disputeId: order.disputes.find((item) => item.status === "open")?.id ?? "",
    settlementId: order.settlements[0]?.id ?? "",
  }));
  const fields = fieldsFor(kind);
  function submit(event: FormEvent) { event.preventDefault(); if (fields.some((field) => field.required && !values[field.key]?.trim())) return; void onExecute(kind, values); }
  return <form onSubmit={submit} className={`rounded-2xl border p-5 ${["freeze", "resume", "pay"].includes(kind) ? "border-amber-300/25 bg-amber-300/[.05]" : "border-white/10 bg-white/[.035]"}`}><h3 className="font-serif text-lg text-white">{labelFor(kind)}</h3>{["freeze", "resume", "pay"].includes(kind) && <p className="mt-2 flex gap-2 text-xs leading-5 text-amber-100"><CircleAlert size={15} className="shrink-0" />账务未部署时服务端会拒绝该资金动作，不会改变结算状态。</p>}<div className="mt-4 space-y-3">{fields.map((field) => <label key={field.key} className="block text-xs text-slate-400">{field.label}<Field field={field} value={values[field.key] ?? ""} onChange={(value) => setValues((old) => ({ ...old, [field.key]: value }))} /></label>)}<button disabled={busy} className="w-full rounded-xl bg-teal-300 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:opacity-50">{busy ? "处理中…" : labelFor(kind)}</button></div></form>;
}
function Field({ field, value, onChange }: { field: { key: string; multiline?: boolean; type?: string }; value: string; onChange: (value: string) => void }) { const classes = "mt-1.5 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-teal-200"; return field.multiline ? <textarea value={value} onChange={(event) => onChange(event.target.value)} className={`${classes} min-h-20`} /> : <input value={value} onChange={(event) => onChange(event.target.value)} className={classes} type={field.type ?? "text"} />; }
function fieldsFor(kind: CommandKind): { key: string; label: string; required?: boolean; multiline?: boolean; type?: string }[] { if (kind === "quote") return [{ key: "amountMinor", label: "报价（分）", required: true, type: "number" }, { key: "currency", label: "币种", required: true }, { key: "proposal", label: "报价说明", required: true, multiline: true }, { key: "validUntil", label: "有效期", required: true, type: "datetime-local" }]; if (kind === "contract") return [{ key: "contentRef", label: "合同证据引用", required: true }, { key: "contentDigest", label: "合同摘要", required: true }, { key: "amountMinor", label: "合同金额（分）", required: true, type: "number" }]; if (kind === "delivery") return [{ key: "milestoneId", label: "里程碑 ID", required: true }, { key: "artifactVersionId", label: "受控交付版本 ID", required: true }, { key: "artifactDigest", label: "交付摘要", required: true }, { key: "note", label: "交付说明", multiline: true }]; if (kind === "accept") return [{ key: "deliveryId", label: "交付 ID", required: true }, { key: "evidenceRef", label: "验收证据引用", required: true }]; if (kind === "return") return [{ key: "deliveryId", label: "交付 ID", required: true }, { key: "reason", label: "退回原因", required: true, multiline: true }]; if (kind === "dispute") return [{ key: "milestoneId", label: "里程碑 ID", required: true }, { key: "kind", label: "争议类型", required: true }, { key: "reason", label: "争议说明", required: true, multiline: true }]; if (kind === "resolve") return [{ key: "disputeId", label: "争议 ID", required: true }, { key: "resolution", label: "处理结论", required: true, multiline: true }]; return [{ key: "settlementId", label: "结算 ID", required: true }, { key: "amountMinor", label: "确认金额（分）", required: true, type: "number" }, { key: "currency", label: "币种", required: true }]; }
function labelFor(kind: CommandKind): string { return ({ quote: "提交报价", contract: "记录合同", delivery: "提交交付", accept: "确认验收", return: "退回修改", dispute: "发起争议", resolve: "处理争议", freeze: "冻结结算", resume: "恢复结算", pay: "确认付款" })[kind]; }
