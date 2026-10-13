import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { collaborationCommerceNamespace } from "./i18n";
import type { CommercePageQuery } from "./types";

const dimensions = ["projectId", "modelId", "actorId", "episodeId", "shotId"] as const;

function localTime(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function BillingFilters({ value, projectId, costs, onApply }: {
  readonly value: CommercePageQuery;
  readonly projectId?: string;
  readonly costs: boolean;
  readonly onApply: (value: CommercePageQuery) => void;
}) {
  const { t } = useTranslation(collaborationCommerceNamespace);
  const [draft, setDraft] = useState<CommercePageQuery>(value);
  const [from, setFrom] = useState(localTime(value.dateFrom));
  const [to, setTo] = useState(localTime(value.dateTo));
  const invalidRange = Boolean(from && to && new Date(from) >= new Date(to));

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (invalidRange) return;
    onApply({
      ...draft,
      projectId: projectId ?? draft.projectId,
      dateFrom: from ? new Date(from).toISOString() : undefined,
      dateTo: to ? new Date(to).toISOString() : undefined,
    });
  };

  return (
    <form className="xj-cc-billing-filters" aria-label={t("billing.filters")} onSubmit={submit}>
      {costs && dimensions.map((key) => <label className="xj-cc-field" key={key}>
        <span>{t(`billing.${key}`)}</span>
        <input value={key === "projectId" && projectId ? projectId : draft[key] ?? ""} readOnly={key === "projectId" && Boolean(projectId)} onChange={(event) => setDraft((current) => ({ ...current, [key]: event.target.value.trim() || undefined }))} />
      </label>)}
      {costs && <label className="xj-cc-field"><span>{t("billing.source")}</span>
        <select value={draft.source ?? ""} onChange={(event) => setDraft((current) => ({ ...current, source: event.target.value || undefined }))}>
          <option value="">{t("billing.allSources")}</option>
          {(["generation", "audio", "editing"] as const).map((source) => <option key={source} value={source}>{t(`billing.${source}`)}</option>)}
        </select>
      </label>}
      <label className="xj-cc-field"><span>{t("billing.dateFrom")}</span><input type="datetime-local" value={from} onChange={(event) => setFrom(event.target.value)} /></label>
      <label className="xj-cc-field"><span>{t("billing.dateTo")}</span><input type="datetime-local" value={to} onChange={(event) => setTo(event.target.value)} /></label>
      <div className="xj-cc-actions">
        <button type="submit" className="xj-cc-button" disabled={invalidRange}>{t("billing.apply")}</button>
        <button type="button" className="xj-cc-button" onClick={() => { setDraft({}); setFrom(""); setTo(""); onApply({}); }}>{t("common.clear")}</button>
      </div>
      {invalidRange && <p role="alert">{t("billing.invalidRange")}</p>}
    </form>
  );
}
