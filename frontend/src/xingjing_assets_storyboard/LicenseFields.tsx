import { useId, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { useAssetLibraryTranslation } from "./asset-library-i18n";

// A blank date is an unfinished fixed term. Only null represents an open-ended permission.
export type LicenseEnd = string | null;

function localDay(date: Date) {
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
}

export function earliestLicenseEnd(now = new Date()) {
  // The server accepts a date through the end of its UTC day.
  const today = localDay(now);
  const utcToday = now.toISOString().slice(0, 10);
  return today > utcToday ? today : utcToday;
}

function monthsFromToday(months: number, now: Date) {
  const endOfMonth = new Date(now.getFullYear(), now.getMonth() + months + 1, 0);
  endOfMonth.setDate(Math.min(now.getDate(), endOfMonth.getDate()));
  return localDay(endOfMonth);
}

export function LicenseTermField({ value, onChange }: { value: LicenseEnd; onChange: (value: LicenseEnd) => void }) {
  const { t } = useAssetLibraryTranslation();
  const group = useId();
  const now = new Date();
  const lastFixedDate = useRef(value ?? "");
  function chooseDate(date: string) {
    lastFixedDate.current = date;
    onChange(date);
  }
  return (
    <fieldset className="xj-license-term">
      <legend>{t("term")}</legend>
      <div className="xj-license-modes">
        <label><input type="radio" name={group} checked={value === null} onChange={() => onChange(null)} />
          <span>{t("openTerm")}</span></label>
        <label><input type="radio" name={group} checked={value !== null} onChange={() => chooseDate(lastFixedDate.current)} />
          <span>{t("fixedTerm")}</span></label>
      </div>
      {value !== null && (
        <div className="xj-license-date">
          <label><span>{t("until")}</span>
            <input type="date" required min={earliestLicenseEnd(now)} value={value}
              onChange={(event) => chooseDate(event.target.value)} />
          </label>
          <div className="xj-license-shortcuts">
            {([[1, "afterMonth"], [3, "afterQuarter"], [12, "afterYear"]] as const).map(([months, label]) => {
              const end = monthsFromToday(months, now);
              return <button key={label} type="button" className="xj-lib-btn" aria-pressed={value === end}
                onClick={() => chooseDate(end)}>{t(label)}</button>;
            })}
          </div>
        </div>
      )}
    </fieldset>
  );
}

const acceptedTypes = ["application/pdf", "image/jpeg", "image/png", "image/webp"];

export function LicenseProofField({ file, uploaded, onChange }: {
  file?: File;
  uploaded?: boolean;
  onChange: (file?: File) => void;
}) {
  const { t } = useAssetLibraryTranslation();
  const ref = useRef<HTMLInputElement>(null);
  const id = useId();
  const [invalid, setInvalid] = useState(false);
  function select(next?: File) {
    const rejected = Boolean(next && (!next.size || next.size > 25 * 1024 * 1024 || !acceptedTypes.includes(next.type)));
    ref.current?.setCustomValidity(rejected ? t("fileError") : "");
    setInvalid(rejected);
    onChange(next);
  }
  return (
    <div className="xj-proof-upload">
      <label htmlFor={id}><span><Upload size={16} aria-hidden="true" />{t("optionalProof")}</span></label>
      <small id={id + "-hint"}>{t("proofHint")}</small>
      <input ref={ref} id={id} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp"
        aria-describedby={id + (invalid ? "-error" : "-hint")} aria-invalid={invalid || undefined}
        onChange={(event) => select(event.currentTarget.files?.[0])} />
      {invalid && <small id={id + "-error"} role="alert">{t("fileError")}</small>}
      {uploaded && !file && <small>{t("attachedProof")}</small>}
      {(file || uploaded) && <button type="button" className="xj-lib-textbtn" onClick={() => {
        if (ref.current) ref.current.value = "";
        select();
      }}>{t("removeProof")}</button>}
    </div>
  );
}
