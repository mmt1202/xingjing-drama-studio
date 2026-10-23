import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { ModelApiError, type ModelAction, type ModelOperationsApi, type ModelRecord, type OperationsPage, type Policy } from "./api";
import { modelButton, modelField, useModelTranslation } from "./i18n";

export interface FormProps { api: ModelOperationsApi; onSaved: () => void; onCancel: () => void; onReload: () => void }
const lines = (value: string) => value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
export function errorKey(error: unknown) {
  if (!(error instanceof ModelApiError)) return error instanceof Error && error.message.startsWith("INVALID_") ? "inputError" : "failed";
  if (error.status === 403 || error.status === 401) return "forbidden";
  if (error.status === 404) return error.code === "WORKSPACE_NOT_FOUND" ? "scopeError" : "missing";
  if (error.code === "PRICING_VERSION_EXISTS") return "priceExists";
  if (error.status === 409) return "conflict";
  if (error.code.includes("ACTIVATION")) return "activationError";
  if (error.code.includes("REFERENCE") || error.code.includes("NO_ACTIVE_MODEL")) return "referenceError";
  return error.status === 422 ? "inputError" : "failed";
}
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="grid min-w-0 gap-2 text-xs text-slate-400">{label}{children}</label>; }

function CommandPanel({ api, title, children, command, onSaved, onCancel, onReload }: FormProps & { title: string; children: ReactNode; command: (reason: string) => ModelAction }) {
  const { t } = useModelTranslation();
  const [reason, setReason] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>();
  const pending = useRef<{ body: string; key: string }>(undefined);
  const conflict = error instanceof ModelApiError && error.status === 409 && error.code !== "PRICING_VERSION_EXISTS";
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy || conflict) return;
    setBusy(true); setError(undefined);
    try {
      const value = command(reason.trim()); const body = JSON.stringify(value);
      if (pending.current?.body !== body) pending.current = { body, key: crypto.randomUUID() };
      await api.save(value, pending.current.key); onSaved();
    } catch (problem) { setError(problem); }
    finally { setBusy(false); }
  }
  return <section className="rounded-2xl border border-orange-300/25 bg-[#11141D] p-5"><h2 className="text-xl font-semibold">{title}</h2>
    <form className="mt-5" onSubmit={event => void submit(event)}><fieldset className="grid gap-4" disabled={busy || conflict}>{children}<Field label={t("reason")}><textarea className={modelField} rows={2} value={reason} onChange={event => setReason(event.target.value)} maxLength={1000} required /></Field></fieldset>
      {Boolean(error) && <div role="alert" className="mt-4 rounded-xl border border-amber-300/25 p-3 text-sm text-amber-200"><p>{t(errorKey(error))}</p>{conflict && <button type="button" className={modelButton + " mt-3"} onClick={onReload}>{t("reloadDiscard")}</button>}</div>}
      <div className="mt-5 flex flex-wrap gap-2"><button className={modelButton + " border-orange-300/30 bg-orange-400/10 text-orange-200"} disabled={busy || conflict}>{t(busy ? "saving" : "save")}</button><button type="button" className={modelButton} onClick={onCancel} disabled={busy}>{t("cancel")}</button></div>
    </form>
  </section>;
}

type Parameter = { id: string; name: string; type: string; values: string };
export function ModelEditor({ model, ...props }: FormProps & { model?: ModelRecord }) {
  const { t } = useModelTranslation();
  const [key, setKey] = useState(model?.registryKey ?? ""); const [provider, setProvider] = useState(model?.providerId ?? "");
  const [providerName, setProviderName] = useState(model?.providerModelName ?? ""); const [name, setName] = useState(model?.displayName ?? "");
  const [version, setVersion] = useState(model?.modelVersion ?? ""); const [region, setRegion] = useState(model?.region ?? "");
  const [media, setMedia] = useState(model?.mediaTypes ?? []); const [capabilities, setCapabilities] = useState(model?.capabilities.join("\n") ?? "");
  const [concurrency, setConcurrency] = useState(model ? String(model.maxConcurrency) : "");
  const [credentialMode, setCredentialMode] = useState("keep"); const [credential, setCredential] = useState("");
  const [parameters, setParameters] = useState<Parameter[]>(() => Object.entries(model?.parameterSchema ?? {}).map(([name, value]) => ({ id: crypto.randomUUID(), name, type: value.type, values: value.enum?.map(item => String(item)).join("\n") ?? "" })));
  function command(reason: string): ModelAction {
    const schema: Record<string, { type: string; enum?: unknown[] }> = {};
    for (const item of parameters) {
      if (!item.name.trim() || schema[item.name.trim()]) throw new Error("INVALID_PARAMETER");
      const values = lines(item.values).map(value => {
        if (item.type === "string") return value;
        if (item.type === "boolean") { if (!["true", "false"].includes(value)) throw new Error("INVALID_BOOLEAN"); return value === "true"; }
        const number = Number(value); if (!Number.isFinite(number) || item.type === "integer" && !Number.isInteger(number)) throw new Error("INVALID_NUMBER"); return number;
      });
      schema[item.name.trim()] = { type: item.type, ...(values.length ? { enum: values } : {}) };
    }
    const payload = { providerId: provider.trim(), providerModelName: providerName.trim(), displayName: name.trim(), modelVersion: version.trim(), region: region.trim(), mediaTypes: media, capabilities: lines(capabilities), parameterSchema: schema, maxConcurrency: Number(concurrency), active: model?.active ?? false,
      ...(!model || credentialMode === "replace" ? credential.trim() ? { credentialRef: credential.trim() } : {} : credentialMode === "clear" ? { credentialRef: null } : {}) };
    return { action: "upsert_model", registryKey: key.trim(), version: model?.version ?? 0, reason, payload };
  }
  function updateParameter(id: string, value: Partial<Parameter>) { setParameters(current => current.map(item => item.id === id ? { ...item, ...value } : item)); }
  return <CommandPanel {...props} title={t(model ? "edit" : "create")} command={command}>
    <p className="text-xs leading-6 text-slate-500">{t("modelHint")}</p>
    <Field label={t("key")}><input className={modelField} value={key} onChange={event => setKey(event.target.value)} required maxLength={128} readOnly={Boolean(model)} /></Field>
    <Field label={t("name")}><input className={modelField} value={name} onChange={event => setName(event.target.value)} required maxLength={255} /></Field>
    <div className="grid gap-4 sm:grid-cols-2"><Field label={t("providerId")}><input className={modelField} readOnly={Boolean(model)} value={provider} onChange={event => setProvider(event.target.value)} required maxLength={64} /></Field><Field label={t("providerModelName")}><input className={modelField} readOnly={Boolean(model)} value={providerName} onChange={event => setProviderName(event.target.value)} required maxLength={255} /></Field><Field label={t("modelVersion")}><input className={modelField} value={version} onChange={event => setVersion(event.target.value)} required maxLength={128} /></Field><Field label={t("region")}><input className={modelField} value={region} onChange={event => setRegion(event.target.value)} required maxLength={64} /></Field></div>
    <fieldset><legend className="mb-2 text-xs text-slate-400">{t("media")}</legend><div className="flex flex-wrap gap-4">{["image", "video", "audio", "text"].map(value => <label key={value} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={media.includes(value)} onChange={event => setMedia(current => event.target.checked ? [...current, value] : current.filter(item => item !== value))} />{t(value)}</label>)}</div></fieldset>
    <Field label={t("capabilities")}><textarea className={modelField} rows={2} value={capabilities} onChange={event => setCapabilities(event.target.value)} required /></Field>
    <Field label={t("concurrency")}><input className={modelField} type="number" min={1} max={10000} step={1} value={concurrency} onChange={event => setConcurrency(event.target.value)} required /></Field>
    {model && <Field label={t("credential")}><select className={modelField} value={credentialMode} onChange={event => setCredentialMode(event.target.value)}><option value="keep">{t("credentialKeep")}</option><option value="replace">{t("credentialReplace")}</option><option value="clear">{t("credentialClear")}</option></select></Field>}
    {(!model || credentialMode === "replace") && <Field label={t("credential")}><input className={modelField} value={credential} onChange={event => setCredential(event.target.value)} maxLength={128} required={credentialMode === "replace"} autoComplete="off" /><span>{t("credentialHint")}</span></Field>}
    <fieldset className="grid gap-3 border-t border-white/10 pt-4"><legend className="text-sm">{t("parameters")}</legend>{parameters.map(item => <div key={item.id} className="grid gap-3 rounded-xl border border-white/10 p-3"><Field label={t("parameterName")}><input className={modelField} value={item.name} onChange={event => updateParameter(item.id, { name: event.target.value })} required maxLength={128} /></Field><Field label={t("parameterType")}><select className={modelField} value={item.type} onChange={event => updateParameter(item.id, { type: event.target.value })}>{["string", "integer", "number", "boolean"].map(type => <option key={type} value={type}>{t(type)}</option>)}</select></Field><Field label={t("allowedValues")}><textarea className={modelField} rows={2} value={item.values} onChange={event => updateParameter(item.id, { values: event.target.value })} /></Field><button type="button" className={modelButton} onClick={() => setParameters(current => current.filter(row => row.id !== item.id))}>{t("remove")}</button></div>)}<button type="button" className={modelButton} onClick={() => setParameters(current => [...current, { id: crypto.randomUUID(), name: "", type: "string", values: "" }])}>{t("addParameter")}</button></fieldset>
  </CommandPanel>;
}

export function PriceEditor({ model, ...props }: FormProps & { model: ModelRecord }) {
  const { t } = useModelTranslation();
  const [version, setVersion] = useState(""); const [currency, setCurrency] = useState(model.pricing?.currency ?? ""); const [amount, setAmount] = useState("");
  return <CommandPanel {...props} title={t("price")} command={reason => ({ action: "publish_pricing", registryKey: model.registryKey, version: model.version, reason, payload: { pricingVersion: version.trim(), currency: currency.trim().toUpperCase(), estimatedMinor: Number(amount) } })}>
    <p className="break-all text-sm text-orange-200">{model.displayName} · {model.registryKey}</p><p className="text-xs leading-6 text-slate-500">{t("priceHint")}</p>
    <Field label={t("priceVersion")}><input className={modelField} value={version} onChange={event => setVersion(event.target.value)} required maxLength={64} /></Field><Field label={t("currency")}><input className={modelField} value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())} required minLength={3} maxLength={3} pattern="[A-Z]{3}" /></Field><Field label={t("amount")}><input className={modelField} type="number" min={1} max={2147483647} step={1} value={amount} onChange={event => setAmount(event.target.value)} required /></Field>
  </CommandPanel>;
}

export function AvailabilityEditor({ model, ...props }: FormProps & { model: ModelRecord }) {
  const { t } = useModelTranslation();
  return <CommandPanel {...props} title={t(model.active ? "disable" : "enable")} command={reason => ({ action: "set_model_active", registryKey: model.registryKey, version: model.version, reason, payload: { active: !model.active } })}>
    <p className="break-all text-sm text-orange-200">{model.displayName} · {model.registryKey}</p><p className="text-sm leading-7 text-slate-400">{t(model.active ? "disableHint" : "enableHint")}</p>
  </CommandPanel>;
}

export function PolicyEditor({ policy, data, ...props }: FormProps & { policy?: Policy; data: OperationsPage }) {
  const { t } = useModelTranslation(); const catalog = data.catalog;
  const [capability, setCapability] = useState(policy?.capability ?? ""); const [media, setMedia] = useState(policy?.mediaType ?? "");
  const [allowed, setAllowed] = useState(policy?.allowedRegistryKeys ?? []); const [preferred, setPreferred] = useState(policy?.preferredRegistryKeys.join("\n") ?? "");
  const [mode, setMode] = useState(policy?.routingOptions.mode ?? "priority"); const [weights, setWeights] = useState<Record<string, number>>(policy?.routingOptions.weights ?? {});
  const [fallback, setFallback] = useState(policy?.allowFallback ?? false); const [currency, setCurrency] = useState(policy?.routingOptions.currency ?? "");
  const [cost, setCost] = useState(policy?.maxEstimatedMinor?.toString() ?? ""); const [latency, setLatency] = useState(policy?.routingOptions.maxLatencyMs?.toString() ?? ""); const [failures, setFailures] = useState(policy?.routingOptions.maxConsecutiveFailures?.toString() ?? ""); const [success, setSuccess] = useState(policy?.routingOptions.minSuccessRate?.toString() ?? "");
  const options = catalog.filter(model => (!capability || model.capabilities.includes(capability)) && (!media || model.mediaTypes.includes(media)));
  function scopeChange(nextCapability: string, nextMedia: string) { setCapability(nextCapability); setMedia(nextMedia); setAllowed([]); setPreferred(""); setWeights({}); }
  function command(reason: string): ModelAction {
    const primary = lines(preferred); const weightedKeys = primary.length ? primary : allowed;
    const routingOptions = { mode, weights: mode === "weighted" ? Object.fromEntries(weightedKeys.map(key => [key, weights[key] ?? 0])) : {}, currency: currency.trim() || null, maxLatencyMs: latency ? Number(latency) : null, maxConsecutiveFailures: failures ? Number(failures) : null, minSuccessRate: success ? Number(success) : null };
    return { action: "upsert_policy", version: policy?.version ?? 0, reason, payload: { capability, mediaType: media, allowedRegistryKeys: allowed, preferredRegistryKeys: primary, maxEstimatedMinor: cost ? Number(cost) : null, allowFallback: fallback, routingOptions } };
  }
  const primary = lines(preferred); const weightedKeys = primary.length ? primary : allowed;
  return <CommandPanel {...props} title={t("routes")} command={command}>
    <Field label={t("capability")}><select className={modelField} value={capability} disabled={Boolean(policy)} onChange={event => scopeChange(event.target.value, media)} required><option value="">—</option>{[...new Set(catalog.flatMap(model => model.capabilities))].sort().map(value => <option key={value}>{value}</option>)}</select></Field><Field label={t("media")}><select className={modelField} value={media} disabled={Boolean(policy)} onChange={event => scopeChange(capability, event.target.value)} required><option value="">—</option>{["image", "video", "audio", "text"].map(value => <option key={value} value={value}>{t(value)}</option>)}</select></Field>
    <fieldset className="grid max-h-64 gap-3 overflow-auto rounded-xl border border-white/10 p-3"><legend className="text-xs text-slate-400">{t("allowedModels")}</legend>{options.map(model => <label key={model.registryKey} className="flex items-start gap-2 text-xs"><input type="checkbox" checked={allowed.includes(model.registryKey)} onChange={event => { setAllowed(current => event.target.checked ? [...current, model.registryKey] : current.filter(key => key !== model.registryKey)); }} /><span className="min-w-0 break-all">{model.displayName}<span className="mt-1 block font-mono text-slate-500">{model.registryKey} · {t(model.active ? "active" : "disabled")}</span></span></label>)}</fieldset>
    <Field label={t("mode")}><select className={modelField} value={mode} onChange={event => setMode(event.target.value as "priority" | "weighted")}><option value="priority">{t("priority")}</option><option value="weighted">{t("weighted")}</option></select></Field><Field label={t("preferred")}><textarea className={modelField} rows={3} value={preferred} onChange={event => setPreferred(event.target.value)} /><span>{t("preferredHint")}</span></Field>
    {mode === "weighted" && <div className="grid gap-3">{weightedKeys.map(key => <Field key={key} label={`${key} · ${t("weight")}`}><input className={modelField} type="number" min={1} max={100} step={1} value={weights[key] ?? ""} onChange={event => setWeights(current => ({ ...current, [key]: Number(event.target.value) }))} required /></Field>)}<p className="text-xs text-slate-500">{t("weightError")}</p></div>}
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={fallback} onChange={event => setFallback(event.target.checked)} />{t("fallback")}</label>
    <Field label={t("currency")}><input className={modelField} maxLength={3} minLength={3} pattern="[A-Z]{3}" value={currency} onChange={event => setCurrency(event.target.value.toUpperCase())} required={Boolean(cost)} /><span>{t("costCurrencyHint")}</span></Field><Field label={t("maxCost")}><input className={modelField} type="number" min={1} max={2147483647} step={1} value={cost} onChange={event => setCost(event.target.value)} /></Field>
    <h3 className="border-t border-white/10 pt-4 text-sm">{t("thresholds")}</h3><p className="text-xs leading-6 text-slate-500">{t("thresholdsHint", { size: data.routingLimits.successSampleSize, minimum: data.routingLimits.minimumSuccessSamples, ceiling: data.routingLimits.failureCeiling })}</p>
    <Field label={t("latency")}><input className={modelField} type="number" min={1} max={3600000} step={1} value={latency} onChange={event => setLatency(event.target.value)} /></Field><Field label={t("failures")}><input className={modelField} type="number" min={1} max={data.routingLimits.failureCeiling} step={1} value={failures} onChange={event => setFailures(event.target.value)} /></Field><Field label={t("successRate")}><input className={modelField} type="number" min={0} max={100} step="any" value={success} onChange={event => setSuccess(event.target.value)} /></Field>
  </CommandPanel>;
}

export function ModelDetail({ model, onClose }: { model: ModelRecord; onClose: () => void }) {
  const { t, i18n } = useModelTranslation(); const date = (value: string | null | undefined) => value ? new Date(value).toLocaleString(i18n.resolvedLanguage) : t("noObservation");
  return <section className="rounded-2xl border border-violet-300/25 bg-[#11141D] p-5"><div className="flex items-start justify-between gap-3"><h2 className="text-xl font-semibold">{model.displayName}</h2><button className={modelButton} onClick={onClose}>{t("close")}</button></div><p className="mt-3 break-all font-mono text-xs text-slate-500">{model.registryKey}</p><dl className="mt-5 grid grid-cols-2 gap-4 text-xs">{[["version", model.version], ["modelVersion", model.modelVersion], ["activeCalls", model.health.activeCalls], ["lastLatency", model.health.latencyMs == null ? t("noObservation") : `${model.health.latencyMs} ms`], ["failureCount", model.health.consecutiveFailures], ["updated", date(model.updatedAt)], ["checked", date(model.health.checkedAt)], ["circuit", date(model.health.circuitOpenUntil)]].map(([label, value]) => <div key={label}><dt className="text-slate-500">{t(String(label))}</dt><dd className="mt-2 break-all">{value}</dd></div>)}</dl><p className="mt-5 text-xs text-slate-400">{t(model.credentialConfigured ? "configured" : "unconfigured")}</p><p className="mt-2 text-xs leading-6 text-slate-500">{t("referenceOnly")}</p><h3 className="mt-6 border-t border-white/10 pt-4 text-sm">{t("history")}</h3><ul className="mt-3 divide-y divide-white/10">{model.priceHistory?.map(price => <li key={price.id} className="py-3 text-xs"><p className="font-mono text-orange-200">{price.pricingVersion} · {price.currency} {price.estimatedMinor}</p><p className="mt-2 text-slate-500">{t("effective")} · {date(price.effectiveAt)}</p><p className="mt-1 text-slate-500">{t("retired")} · {price.retiredAt ? date(price.retiredAt) : "—"}</p></li>)}</ul></section>;
}
