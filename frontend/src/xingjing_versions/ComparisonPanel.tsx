import { useId, type ReactNode } from "react";
import { ArrowRightLeft, ChevronDown, Download, FileDiff, LoaderCircle } from "lucide-react";
import { useVersionTranslation, versionButton } from "./i18n";
import "./versions.css";

interface VersionLabel { label: string; versionNumber: number; versionId: string }
interface VersionChange { path: string; kind: "added" | "removed" | "changed"; before?: unknown; after?: unknown }
interface ComparisonPanelProps {
  selectors?: ReactNode; baseline?: VersionLabel; candidate?: VersionLabel;
  changes?: readonly VersionChange[]; busy: boolean; error?: ReactNode;
  onCompare: () => void; onDownloadReport: () => void; canCompare: boolean; canDownload: boolean;
}

function DifferenceValue({ value }: { value: unknown }) {
  const { t } = useVersionTranslation();
  let text = "—";
  if (typeof value === "string") text = value || t("emptyText");
  else if (typeof value === "boolean") text = t(value ? "booleanTrue" : "booleanFalse");
  else if (typeof value === "number" && Number.isFinite(value)) text = String(value);
  else if (value !== null && typeof value === "object") {
    try { text = JSON.stringify(value, null, 2) ?? "—"; } catch { text = "—"; }
  }
  const structured = value !== null && typeof value === "object";
  const expandable = structured || text.length > 180 || text.split("\n").length > 4;
  if (!expandable) return <p className="xj-versions-value">{text}</p>;
  return <details className="xj-versions-value-details">
    <summary>
      {!structured ? <span className="xj-versions-value-preview">{text.length > 240 ? `${text.slice(0, 240)}…` : text}</span> : null}
      <span className="xj-versions-value-toggle"><ChevronDown size={13} aria-hidden="true" /><span className="xj-versions-value-expand">{t(structured ? "structuredValue" : "expandValue")}</span><span className="xj-versions-value-collapse">{t("collapseValue")}</span></span>
    </summary>
    <pre className="xj-versions-value xj-versions-value-full">{text}</pre>
  </details>;
}

function VersionCard({ version, side }: { version?: VersionLabel; side: "baseline" | "candidate" }) {
  const { t } = useVersionTranslation();
  return <div className="xj-versions-compare-version">
    <p className="xj-versions-caption">{t(side)}</p>
    <p className="xj-versions-compare-label">{version?.label || t(side === "baseline" ? "chooseBaseline" : "chooseCandidate")}</p>
    {version ? <><p className="xj-versions-compare-number">{Number.isFinite(version.versionNumber) ? t("versionNumber", { number: version.versionNumber }) : "—"}</p><code className="xj-versions-compare-id" title={version.versionId} aria-label={t("versionId")}>{version.versionId || "—"}</code></> : null}
  </div>;
}

export function ComparisonPanel({ selectors, baseline, candidate, changes, busy, error, onCompare, onDownloadReport, canCompare, canDownload }: ComparisonPanelProps) {
  const { t } = useVersionTranslation();
  const headingId = useId();
  const fieldLabel = (path: string) => path.split("/").filter(Boolean).map((part) => {
    const decoded = part.replace(/~1/g, "/").replace(/~0/g, "~");
    return /^\d+$/.test(decoded) ? t("fieldItem", { number: Number(decoded) + 1 }) : t(`field.${decoded}`, { defaultValue: decoded });
  }).join(" / ") || t("wholeContent");
  return <aside className="xj-versions-panel xj-versions-inspector" aria-labelledby={headingId} aria-busy={busy}>
    <div className="xj-versions-toolbar"><h2 id={headingId} className="xj-versions-section-title">{t("compareTitle")}</h2><ArrowRightLeft size={17} aria-hidden="true" /></div>
    {selectors ? <div className="xj-versions-selectors">{selectors}</div> : null}
    <p className="xj-versions-help">{t("businessDiffHint")}</p>
    <div className="xj-versions-compare-pair"><VersionCard version={baseline} side="baseline" /><VersionCard version={candidate} side="candidate" /></div>
    <button type="button" className={`${versionButton} xj-versions-button--primary xj-versions-compare-action`} disabled={busy || !canCompare} onClick={onCompare}>
      {busy ? <LoaderCircle size={15} aria-hidden="true" className="xj-versions-spin" /> : <FileDiff size={15} aria-hidden="true" />}{t(busy ? "comparing" : "compare")}
    </button>
    {error ? <div className="xj-versions-compare-error">{error}</div> : changes === undefined ? <p className="xj-versions-help">{t("comparisonHint")}</p> : <section className="xj-versions-changes" aria-label={t("differences")}>
      <p className="xj-versions-change-count" role="status">{t(changes.length ? "differenceCount" : "noDifferences", { count: changes.length })}</p>
      <ol className="xj-versions-change-list">{changes.map((change) => <li key={`${change.kind}:${change.path}`} className="xj-versions-change">
        <div className="xj-versions-change-heading"><span className="xj-versions-change-path" title={change.path} aria-label={t("fieldPath")}>{fieldLabel(change.path)}</span><span className={`xj-versions-change-kind xj-versions-change-kind--${change.kind}`}>{t(`changeKind.${change.kind}`)}</span></div>
        <dl className="xj-versions-change-values"><div><dt>{t("before")}</dt><dd><DifferenceValue value={change.before} /></dd></div><div><dt>{t("after")}</dt><dd><DifferenceValue value={change.after} /></dd></div></dl>
      </li>)}</ol>
    </section>}
    <button type="button" className={`${versionButton} xj-versions-compare-action`} disabled={busy || !canDownload} onClick={onDownloadReport}><Download size={15} aria-hidden="true" />{t("downloadReport")}</button>
  </aside>;
}
