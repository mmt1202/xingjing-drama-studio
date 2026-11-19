import { useCallback, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Popover } from "./Popover";
import { usePaginationTranslation } from "./pagination-i18n";
import "./pagination.css";

interface PaginationProps {
  offset: number;
  pageSize: number;
  total: number;
  onOffsetChange: (offset: number) => void;
  label?: string;
  unit?: "items" | "episodes" | "chapters" | "documents";
  disabled?: boolean;
}

function pageLinks(current: number, last: number): Array<number | string> {
  if (last <= 7) return Array.from({ length: last }, (_, i) => i + 1);
  const firstNeighbor = Math.max(2, Math.min(current - 1, last - 2));
  const lastNeighbor = Math.min(last - 1, Math.max(current + 1, 3));
  const links: Array<number | string> = [1];
  if (firstNeighbor === 3) links.push(2);
  if (firstNeighbor > 3) links.push("before");
  for (let page = firstNeighbor; page <= lastNeighbor; page++) links.push(page);
  if (lastNeighbor === last - 2) links.push(last - 1);
  if (lastNeighbor < last - 2) links.push("after");
  links.push(last);
  return links;
}

function PageArrow({ back, onClick, label }: { back?: boolean; onClick?: () => void; label?: string }) {
  const { t } = usePaginationTranslation();
  const name = label ?? t(back ? "previous" : "next");
  const Icon = back ? ChevronLeft : ChevronRight;
  return <button type="button" className="xj-page-arrow" aria-label={name} title={name} disabled={!onClick} onClick={onClick}>
    <Icon aria-hidden="true" size={17} strokeWidth={1.8} />
  </button>;
}

function JumpToPage({ current, pages, compact, disabled, select }: { current: number; pages: number; compact?: boolean; disabled: boolean; select: (page: number) => void }) {
  const { t } = usePaginationTranslation();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const anchor = useRef<HTMLButtonElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const focusField = useCallback((node: HTMLInputElement | null) => {
    field.current = node;
    node?.focus({ preventScroll: true });
  }, []);
  function close() {
    if (document.getElementById(id)?.contains(document.activeElement)) anchor.current?.focus({ preventScroll: true });
    setOpen(false);
    setValue("");
  }
  function jump() {
    const page = Number(value);
    if (!value || !Number.isSafeInteger(page) || page < 1 || page > pages) {
      field.current?.focus();
      field.current?.reportValidity();
      return;
    }
    close();
    select(page);
  }
  return <>
    <button type="button" ref={anchor} className={compact ? "xj-page-compact" : "xj-page-jump"} disabled={disabled}
      aria-label={compact ? t("position", { page: current, pages }) : t("jump")} aria-haspopup="dialog" aria-expanded={open && !disabled} aria-controls={open && !disabled ? id : undefined}
      onClick={() => open ? close() : setOpen(true)}>
      {compact ? <><strong>{current}</strong><span>/ {pages}</span></> : t("jump")}<ChevronDown size={13} aria-hidden="true" />
    </button>
    <Popover open={open && !disabled} onClose={close} anchorRef={anchor} width="w-auto" className="rounded-xl border border-violet-200/20 shadow-xl" align="end" placement="top-end">
      <div className="xj-page-picker" id={id} role="dialog" aria-label={t("jump")}>
        <label htmlFor={id + "-number"}>{t("input", { pages })}</label>
        <div><input id={id + "-number"} ref={focusField} type="number" inputMode="numeric" min={1} max={pages} step={1} required
          placeholder={String(current)} value={value} onChange={event => setValue(event.target.value)}
          onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); jump(); } }} />
          <button type="button" disabled={!value} onClick={jump}>{t("submit")}</button></div>
      </div>
    </Popover>
  </>;
}

export function Pagination({ offset, pageSize, total, onOffsetChange, label, unit = "items", disabled = false }: PaginationProps) {
  const { t, i18n } = usePaginationTranslation();
  const [nav, setNav] = useState<HTMLElement | null>(null);
  const [compact, setCompact] = useState(false);
  useLayoutEffect(() => {
    if (!nav) return;
    const measure = () => setCompact(nav.clientWidth > 0 && nav.clientWidth < 400);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [nav]);
  if (pageSize < 1 || (offset === 0 && total <= pageSize)) return null;
  const current = Math.floor(offset / pageSize) + 1;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const format = new Intl.NumberFormat(i18n.resolvedLanguage);
  const range = offset < total ? format.format(offset + 1) + "–" + format.format(Math.min(offset + pageSize, total)) : "—";
  function select(page: number) {
    if (!disabled && page >= 1 && page <= pages && page !== current) onOffsetChange((page - 1) * pageSize);
  }
  return <nav ref={setNav} className="xj-pagination" aria-label={label ?? t("navigation")}>
    <div className="xj-page-bar">
      <span className="xj-page-summary" role="status"><strong>{range}</strong><span className="xj-page-slash">/</span>{t(unit, { count: total })}</span>
      <div className="xj-page-links">
        <PageArrow back onClick={!disabled && current > 1 ? () => select(Math.min(current - 1, pages)) : undefined} />
        {compact ? <JumpToPage current={current} pages={pages} disabled={disabled} select={select} compact /> : pageLinks(Math.min(current, pages), pages).map(page => typeof page === "string"
          ? <span className="xj-page-gap" aria-hidden="true" key={page}>…</span>
          : <button type="button" key={page} className="xj-page-number" aria-label={t("page", { page })}
              aria-current={page === current ? "page" : undefined} disabled={disabled} onClick={() => select(page)}>{page}</button>)}
        <PageArrow onClick={!disabled && current < pages ? () => select(current + 1) : undefined} />
      </div>
      {!compact && pages > 7 && <JumpToPage current={current} pages={pages} disabled={disabled} select={select} />}
    </div>
  </nav>;
}

interface SequentialPaginationProps {
  summary?: ReactNode;
  count?: number;
  previous?: () => void;
  next?: () => void;
  disabled?: boolean;
  page?: number;
  label?: string;
  previousLabel?: string;
  nextLabel?: string;
}

export function SequentialPagination({ summary, count, previous, next, disabled, page, label, previousLabel, nextLabel }: SequentialPaginationProps) {
  const { t } = usePaginationTranslation();
  if (!previous && !next) return null;
  return <nav className="xj-pagination" aria-label={label ?? t("navigation")}>
    <div className="xj-page-bar">
      <span className="xj-page-summary" role="status">{summary ?? (count !== undefined && t("onPage", { count }))}</span>
      <div className="xj-page-links">
        <PageArrow back label={previousLabel} onClick={disabled ? undefined : previous} />
        {page !== undefined && <span className="xj-page-position" aria-current="page">{t("page", { page })}</span>}
        <PageArrow label={nextLabel} onClick={disabled ? undefined : next} />
      </div>
    </div>
  </nav>;
}
