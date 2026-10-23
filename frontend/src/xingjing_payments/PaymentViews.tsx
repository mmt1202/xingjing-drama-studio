import { useEffect, useState, type ReactNode } from "react";
import type { BillingWorkspace, PaymentCatalog, PaymentDetail, PaymentOrder, PaymentRefund } from "./contracts";
import { checkoutUrl, money, share } from "./money";
import { paymentButton, paymentPanel, usePaymentTranslation } from "./i18n";
import { PaymentApiError } from "./api";

export function PaymentError({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = usePaymentTranslation();
  const problem = error instanceof PaymentApiError ? error : undefined;
  const key = problem?.status === 403 || problem?.status === 401 ? "forbidden" : problem?.status === 404 ? "missing" : problem?.status === 409 ? "conflict" : "failed";
  return <div className="xj-payments-notice" role="alert"><p>{problem ? t(`reasonCode.${problem.code}`, { defaultValue: t(key) }) : t(key)}</p>
    {problem ? <p className="xj-payments-code">{problem.code}{problem.requestId ? ` · ${t("requestId")}: ${problem.requestId}` : ""}</p> : null}
    {retry ? <button type="button" className={paymentButton} onClick={retry}>{t("retry")}</button> : null}</div>;
}
export function PaymentStatus({ status, refund = false }: { status: string; refund?: boolean }) {
  const { t } = usePaymentTranslation();
  return <span className={`xj-payments-status xj-payments-status--${status.replace(/[^a-z_]/g, "")}`}>{t(`${refund ? "refundState" : "orderState"}.${status}`, { defaultValue: t("stateUnknown") })}</span>;
}
export function PaymentFacts({ items }: { items: readonly [string, ReactNode][] }) {
  return <dl className="xj-payments-facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "—"}</dd></div>)}</dl>;
}
export function dateLabel(value: string | null | undefined, locale: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString(locale, { timeZone: "UTC", hour12: false });
}
export function BillingMetrics({ billing }: { billing?: BillingWorkspace }) {
  const { t, i18n } = usePaymentTranslation();
  const locale = i18n.resolvedLanguage ?? "zh";
  const currency = billing?.moneyUnit.currency;
  const cards = [
    { label: t("available"), value: money(billing?.account?.availableMinor, currency, locale), help: `${t("held")}: ${money(billing?.account?.heldMinor, currency, locale)}`, className: "available" },
    { label: t("monthlyConsumption"), value: money(billing?.consumption.month.settledMinor, currency, locale), help: t("moneyUnit"), className: "consumed" },
    { label: t("budget"), value: "—", help: t("noBudget"), className: "budget" },
    { label: t("remainingDays"), value: "—", help: t("noForecast"), className: "days" },
  ];
  return <div className="xj-payments-metrics">{cards.map((card) => <section className={`xj-payments-metric xj-payments-metric--${card.className}`} key={card.className}><p>{card.label}</p><strong>{card.value}</strong><p>{card.help}</p></section>)}</div>;
}
export function BillingTrend({ billing }: { billing?: BillingWorkspace }) {
  const { t, i18n } = usePaymentTranslation();
  const points = billing?.consumption.last7Days ?? [];
  const maximum = points.reduce((sum, point) => /^\d+$/.test(point.settledMinor ?? "") && BigInt(point.settledMinor!) > sum ? BigInt(point.settledMinor!) : sum, 0n);
  return <section className={paymentPanel}><h2 className="xj-payments-section-title">{t("trend")}</h2><p className="xj-payments-muted">{t("trendHelp")}</p>
    {!billing?.account || !points.length ? <p className="xj-payments-empty">{t("noTrend")}</p> : <div className="xj-payments-chart">{points.map((point) => <div className="xj-payments-chart-column" key={point.date}>
      <span className="xj-payments-chart-amount">{money(point.settledMinor, billing.moneyUnit.currency, i18n.resolvedLanguage ?? "zh")}</span>
      <div className="xj-payments-chart-track"><span className="xj-payments-chart-bar" style={{ height: `${share(point.settledMinor ?? "0", maximum.toString()) * 1.28}px` }} /></div>
      <span className="xj-payments-chart-date">{point.date.slice(5)}<span className="xj-payments-code">{t("settlementCount", { count: point.settlementCount })}</span></span>
    </div>)}</div>}</section>;
}
export function BillingAside({ billing, catalog, disabled, onTopUp }: { billing?: BillingWorkspace; catalog?: PaymentCatalog; disabled: boolean; onTopUp: (offerId: string) => void }) {
  const { t, i18n } = usePaymentTranslation();
  const items = billing?.consumption.bySource ?? [];
  const total = items.reduce((sum, row) => sum + (row.settledMinor && /^\d+$/.test(row.settledMinor) ? BigInt(row.settledMinor) : 0n), 0n).toString();
  return <>
    <section className={paymentPanel + " xj-payments-panel--inspector"}><h2 className="xj-payments-section-title">{t("plan")}</h2>
      {billing?.plan ? <><PaymentFacts items={[[t("planId"), billing.plan.planId], [t("status"), billing.plan.status], [t("seats"), billing.plan.seatLimit], [t("updated"), dateLabel(billing.plan.updatedAt, i18n.resolvedLanguage ?? "zh")]]} />
        <PlanValues label={t("quota")} value={billing.plan.quotas} /><PlanValues label={t("remainingQuota")} value={billing.plan.quotaRemaining} /></> : <p className="xj-payments-muted">{t("noPlan")}</p>}</section>
    <section className={paymentPanel}><h2 className="xj-payments-section-title">{t("distribution")}</h2>
      {!items.length ? <p className="xj-payments-muted">{t("noDistribution")}</p> : <div className="xj-payments-distribution">{items.map((item) => <div key={item.source}><div className="xj-payments-distribution-label"><span>{t(`sourceName.${item.source}`, { defaultValue: item.source })}</span><span>{money(item.settledMinor, billing?.moneyUnit.currency, i18n.resolvedLanguage ?? "zh")}</span></div>
        <div className="xj-payments-meter"><span style={{ width: `${share(item.settledMinor ?? "0", total)}%` }} /></div><span className="xj-payments-code">{t("settlementCount", { count: item.settlementCount })}</span></div>)}</div>}</section>
    <section className={paymentPanel}><h2 className="xj-payments-section-title">{t("quickTopUp")}</h2><p className="xj-payments-muted">{t("quoteHelp")}</p>
      {!catalog?.configured ? <p className="xj-payments-notice">{t("paymentUnavailable")}</p> : null}
      {!catalog?.offers.length ? <p className="xj-payments-muted">{t("noOffers")}</p> : <div className="xj-payments-distribution">{catalog.offers.map((offer) => <button key={offer.id} className="xj-payments-offer" type="button" disabled={disabled || !catalog.configured || !catalog.permissions.canManage || !catalog.channels.length} onClick={() => onTopUp(offer.id)}>
        <span>{offer.label}</span><strong>{money(offer.amountMinor, offer.currency, i18n.resolvedLanguage ?? "zh")}</strong><small>{t("credited")}: {money(offer.creditMinor, offer.currency, i18n.resolvedLanguage ?? "zh")}</small></button>)}</div>}</section>
  </>;
}
function PlanValues({ label, value }: { label: string; value: unknown }) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const items = Object.entries(value).filter(([, amount]) => typeof amount === "number" || typeof amount === "string");
  return items.length ? <PaymentFacts items={items.map(([key, amount]) => [`${label} · ${key}`, String(amount)])} /> : null;
}
export function OrderInspector({ detail, disabled, mutationDisabled = false, onAction, onRefund }: { detail: PaymentDetail; disabled: boolean; mutationDisabled?: boolean; onAction: (kind: "refresh" | "cancel" | "requestRefund", order: PaymentOrder) => void; onRefund: (refund: PaymentRefund) => void }) {
  const { t, i18n } = usePaymentTranslation();
  const order = detail.order, locale = i18n.resolvedLanguage ?? "zh";
  const checkout = checkoutUrl(order.checkout.url);
  const expiry = order.checkout.expiresAt ? Date.parse(order.checkout.expiresAt) : NaN;
  const [now, setNow] = useState(Date.now);
  useEffect(() => { if (!Number.isFinite(expiry)) return; const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, Math.min(2147483647, expiry - Date.now() + 1))); return () => window.clearTimeout(timer); }, [expiry]);
  const expired = !Number.isFinite(expiry) || expiry <= now;
  return <><section className={paymentPanel + " xj-payments-panel--inspector"}><div className="xj-payments-toolbar"><h2 className="xj-payments-section-title">{t("paymentDetails")}</h2><PaymentStatus status={order.status} /></div>
    <PaymentFacts items={[[t("orderId"), order.id], [t("amount"), money(order.amountMinor, order.currency, locale)], [t("credited"), money(order.creditMinor, order.currency, locale)], [t("refunded"), money(order.refundedMinor, order.currency, locale)], [t("refundable"), money(order.refundableMinor, order.currency, locale)], [t("channel"), order.channel], [t("provider"), order.provider], [t("created"), dateLabel(order.createdAt, locale)], [t("updated"), dateLabel(order.updatedAt, locale)], [t("paidAt"), dateLabel(order.paidAt, locale)], [t("expires"), dateLabel(order.expiresAt, locale)], [t("version"), order.version]]} />
    {order.status === "pending" && checkout && !expired ? <><a className={paymentButton + " xj-payments-button--primary"} href={checkout} target="_blank" rel="noopener noreferrer" aria-disabled={disabled} onClick={(event) => { if (disabled || expiry <= Date.now()) { event.preventDefault(); setNow(Date.now()); } }}>{t("checkout")}</a><p className="xj-payments-muted">{t("checkoutHelp")}</p></> : order.status === "pending" ? <p className="xj-payments-muted">{t(expired && checkout ? "checkoutExpired" : "checkoutUnavailable")}</p> : null}
    <div className="xj-payments-actions">{order.actions.canRefresh ? <button className={paymentButton} type="button" disabled={disabled} onClick={() => onAction("refresh", order)}>{t("refreshOrder")}</button> : null}
      {order.actions.canCancel ? <button className={paymentButton} type="button" disabled={disabled || mutationDisabled} onClick={() => onAction("cancel", order)}>{t("cancelOrder")}</button> : null}
      {order.actions.canRequestRefund ? <button className={paymentButton} type="button" disabled={disabled || mutationDisabled} onClick={() => onAction("requestRefund", order)}>{t("requestRefund")}</button> : null}</div>
    {(order.status === "paid" || order.status === "refunded") && order.refundReasonCode ? <p className="xj-payments-notice">{t(`reasonCode.${order.refundReasonCode}`, { defaultValue: t("unavailable") })}<span className="xj-payments-code">{order.refundReasonCode}</span></p> : null}
  </section><section className={paymentPanel}><h2 className="xj-payments-section-title">{t("refundsTab")}</h2>{!detail.refunds.length ? <p className="xj-payments-muted">{t("noRefunds")}</p> : <div className="xj-payments-distribution">{detail.refunds.map((refund) => <button className="xj-payments-offer" type="button" key={refund.id} disabled={disabled} onClick={() => onRefund(refund)}><span>{refund.id}</span><strong>{money(refund.amountMinor, refund.currency, locale)}</strong><PaymentStatus refund status={refund.status} /></button>)}</div>}</section></>;
}
export function RefundInspector({ refund, disabled, onAction }: { refund: PaymentRefund; disabled: boolean; onAction: (kind: "approve" | "reject" | "refundRefresh", refund: PaymentRefund) => void }) {
  const { t, i18n } = usePaymentTranslation();
  const locale = i18n.resolvedLanguage ?? "zh";
  return <section className={paymentPanel + " xj-payments-panel--inspector"}><div className="xj-payments-toolbar"><h2 className="xj-payments-section-title">{t("refundDetails")}</h2><PaymentStatus refund status={refund.status} /></div>
    <PaymentFacts items={[[t("refundId"), refund.id], [t("linkedOrder"), refund.orderId], [t("amount"), money(refund.amountMinor, refund.currency, locale)], [t("requestedBy"), refund.requestedBy], [t("approvedBy"), refund.approvedBy], [t("originalReason"), refund.reason], [t("ticket"), refund.ticketId], [t("created"), dateLabel(refund.createdAt, locale)], [t("updated"), dateLabel(refund.updatedAt, locale)], [t("completed"), dateLabel(refund.completedAt, locale)], [t("version"), refund.version]]} />
    <PaymentFacts items={[[t("approvalBasis"), money(refund.approvalBasisMinor, refund.currency, locale)]]} />
    <p className="xj-payments-muted">{t("fourEyes")}</p>{refund.reasonCode ? <p className="xj-payments-notice">{t(`reasonCode.${refund.reasonCode}`, { defaultValue: t("unavailable") })}{refund.reasonCode === "PAYMENT_REFUND_MFA_REQUIRED" ? <span className="xj-payments-code">{t("mfaBasisHelp")}</span> : null}<span className="xj-payments-code">{refund.reasonCode}</span></p> : null}
    <div className="xj-payments-actions">{refund.actions.canApprove ? <button className={paymentButton + " xj-payments-button--primary"} type="button" disabled={disabled} onClick={() => onAction("approve", refund)}>{t("approve")}</button> : null}
      {refund.actions.canReject ? <button className={paymentButton} type="button" disabled={disabled} onClick={() => onAction("reject", refund)}>{t("reject")}</button> : null}
      {refund.actions.canRefresh ? <button className={paymentButton} type="button" disabled={disabled} onClick={() => onAction("refundRefresh", refund)}>{t("refreshRefund")}</button> : null}</div></section>;
}
export function PaymentHistory({ detail }: { detail: PaymentDetail }) {
  const { t, i18n } = usePaymentTranslation();
  const labels: Record<string, string> = { order_created: "createOrder", refund_requested: "requestRefund", refund_approve: "approve", refund_reject: "reject", refund_refresh: "refreshRefund", gateway_event: "gatewayEvent" };
  return <section className={paymentPanel}><h2 className="xj-payments-section-title">{t("history")}</h2>{!detail.history.length ? <p className="xj-payments-muted">{t("noHistory")}</p> : <ol className="xj-payments-history">{detail.history.map((item, index) => <li key={`${item.createdAt}:${index}`}><strong>{labels[item.action] ? t(labels[item.action]) : t(`actionName.${item.action}`, { defaultValue: item.action })}</strong><span className="xj-payments-code">{item.actorId} · {dateLabel(item.createdAt, i18n.resolvedLanguage ?? "zh")} UTC</span>{item.note ? <p className="xj-payments-muted">{item.note}</p> : null}</li>)}</ol>}</section>;
}
