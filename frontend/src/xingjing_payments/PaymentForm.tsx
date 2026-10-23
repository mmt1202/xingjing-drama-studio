import { useState, type FormEvent } from "react";
import type { PaymentCatalog } from "./contracts";
import type { PaymentDraft } from "./draft-state";
import { money, parseMinor } from "./money";
import { paymentButton, paymentField, paymentPanel, usePaymentTranslation } from "./i18n";

export function PaymentForm({ draft, catalog, busy, enabled, onChange, onSave, onCancel }: {
  draft: PaymentDraft; catalog?: PaymentCatalog; busy: boolean; enabled: boolean;
  onChange: (value: PaymentDraft) => void; onSave: () => Promise<void>; onCancel: () => void;
}) {
  const { t, i18n } = usePaymentTranslation();
  const [invalid, setInvalid] = useState(false);
  const offer = catalog?.offers.find((item) => item.id === draft.offerId);
  const selectedChannel = catalog?.channels.some((item) => item.id === draft.channel);
  const amount = parseMinor(draft.amount, draft.currency);
  const valid = draft.reason.trim().length > 0 && draft.reason.trim().length <= 1000 && (draft.attempted || draft.kind !== "create" || Boolean(offer && selectedChannel && catalog?.configured)) && (draft.kind !== "requestRefund" || (amount !== undefined && BigInt(amount) > 0n && BigInt(amount) <= 9223372036854775807n));
  const title = draft.kind === "create" ? "createOrder" : draft.kind === "cancel" ? "cancelOrder" : draft.kind === "refundRefresh" ? "refreshRefund" : draft.kind;
  const help = draft.kind === "create" ? "createOrderHelp" : draft.kind === "cancel" ? "cancelHelp" : draft.kind === "requestRefund" ? "requestRefundHelp" : draft.kind === "approve" ? "approveHelp" : draft.kind === "reject" ? "rejectHelp" : "refreshRefundHelp";
  const update = (key: keyof PaymentDraft, value: string) => { setInvalid(false); onChange({ ...draft, [key]: value }); };
  async function submit(event: FormEvent) { event.preventDefault(); if (!enabled || busy) return; if (!valid) { setInvalid(true); return; } await onSave(); }
  return <form className={paymentPanel + " xj-payments-form"} onSubmit={(event) => { void submit(event); }}>
    <div className="xj-payments-toolbar"><h2 className="xj-payments-section-title">{t(title)}</h2>{draft.kind !== "create" ? <span className="xj-payments-code">{draft.targetId} · v{draft.expectedVersion}</span> : null}</div><p className="xj-payments-muted">{t(help)}</p>
    {draft.attempted ? <p className="xj-payments-notice">{t("pendingIntentHelp")}</p> : null}
    <fieldset disabled={busy || draft.attempted}>
      {draft.kind === "create" ? <><label className="xj-payments-label">{t("offer")}<select className={paymentField} value={draft.offerId} onChange={(event) => { const next = catalog?.offers.find((item) => item.id === event.target.value); onChange({ ...draft, offerId: event.target.value, currency: next?.currency ?? "" }); }} required>
        <option value="">{t("selectOffer")}</option>{draft.offerId && !offer ? <option value={draft.offerId}>{t("unavailable")} · {draft.offerId}</option> : null}{catalog?.offers.map((item) => <option value={item.id} key={item.id}>{item.label} · {money(item.amountMinor, item.currency, i18n.resolvedLanguage ?? "zh")}</option>)}</select></label>
        {offer ? <PaymentQuote payable={money(offer.amountMinor, offer.currency, i18n.resolvedLanguage ?? "zh")} credited={money(offer.creditMinor, offer.currency, i18n.resolvedLanguage ?? "zh")} /> : null}
        <label className="xj-payments-label">{t("channel")}<select className={paymentField} value={draft.channel} onChange={(event) => update("channel", event.target.value)} required><option value="">{t("selectChannel")}</option>{draft.channel && !selectedChannel ? <option value={draft.channel}>{t("unavailable")} · {draft.channel}</option> : null}{catalog?.channels.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label></> : null}
      {draft.kind === "requestRefund" ? <><label className="xj-payments-label">{t("refundAmount")} · {draft.currency}<input className={paymentField} value={draft.amount} inputMode="decimal" maxLength={40} onChange={(event) => update("amount", event.target.value)} required /></label><label className="xj-payments-label">{t("ticket")}<input className={paymentField} maxLength={128} value={draft.ticketId} onChange={(event) => update("ticketId", event.target.value)} /><span className="xj-payments-muted">{t("ticketHelp")}</span></label></> : null}
      <label className="xj-payments-label">{t("reason")}<textarea className={paymentField} value={draft.reason} maxLength={1000} required onChange={(event) => update("reason", event.target.value)} /></label>
    </fieldset>{invalid ? <p className="xj-payments-notice" role="alert">{t(draft.kind === "requestRefund" && (amount === undefined || BigInt(amount) <= 0n) ? "invalidAmount" : "requiredReason")}</p> : null}
    {!enabled ? <p className="xj-payments-notice">{t("stale")}</p> : null}<div className="xj-payments-actions"><button type="submit" className={paymentButton + " xj-payments-button--primary"} disabled={busy || !enabled}>{t(busy ? "processing" : draft.attempted ? "retryIntent" : "confirm")}</button><button type="button" className={paymentButton} disabled={busy} onClick={onCancel}>{t(draft.attempted ? "hideDraft" : "cancel")}</button></div>
  </form>;
}
function PaymentQuote({ payable, credited }: { payable: string; credited: string }) {
  const { t } = usePaymentTranslation();
  return <div className="xj-payments-form-pair"><div><span className="xj-payments-muted">{t("payable")}</span><strong className="xj-payments-code">{payable}</strong></div><div><span className="xj-payments-muted">{t("credited")}</span><strong className="xj-payments-code">{credited}</strong></div></div>;
}
