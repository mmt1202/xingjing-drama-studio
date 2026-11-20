import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  BookOpen,
  Boxes,
  ChevronRight,
  CircleHelp,
  Clapperboard,
  MapPin,
  Mic,
  Search,
  ShieldCheck,
  Shirt,
  SlidersHorizontal,
  UserRound,
} from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import { useAssetLibraryTranslation } from "./asset-library-i18n";
import type { AssetItem, AssetListQuery, AssetType, RightsStatus } from "./contracts";
import "./asset-library.css";

export const LIBRARY_PAGE_SIZE = 12;
export const assetKinds = { character: UserRound, scene: MapPin, prop: Boxes, costume: Shirt, voice: Mic };
const states: readonly RightsStatus[] = ["missing", "pending", "verified", "expired", "blocked"];
export const rightsAction = {
  missing: "register",
  pending: "review",
  verified: "view",
  expired: "renew",
  blocked: "replace",
} as const;
type BrowseState = { query: AssetListQuery; offset: number; focusAsset?: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function recallLibrary(key: string): BrowseState {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (
      isRecord(saved) &&
      typeof saved.offset === "number" &&
      Number.isSafeInteger(saved.offset) &&
      saved.offset >= 0 &&
      saved.offset <= 2147483647 &&
      saved.offset % LIBRARY_PAGE_SIZE === 0 &&
      isRecord(saved.query)
    ) {
      const { search, assetType, rightsStatus } = saved.query;
      return {
        offset: saved.offset,
        query: {
          search: typeof search === "string" ? search : undefined,
          assetType:
            typeof assetType === "string" && Object.hasOwn(assetKinds, assetType)
              ? (assetType as AssetType)
              : undefined,
          rightsStatus: states.find((state) => state === rightsStatus),
        },
        focusAsset: typeof saved.focusAsset === "string" ? saved.focusAsset : undefined,
      };
    }
  } catch {
    /* Browsing also works without session storage. */
  }
  return { query: {}, offset: 0 };
}
export function rememberLibrary(key: string, value: BrowseState) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Session storage is optional. */
  }
}

interface LibraryProps {
  scopeTitle: string;
  project: boolean;
  episode?: boolean;
  manage: boolean;
  ownerProject?: string;
  assets: readonly AssetItem[];
  total: number;
  offset: number;
  query: AssetListQuery;
  loading: boolean;
  busy: boolean;
  restoreFocus?: string;
  selected: readonly string[];
  manual?: ReactNode;
  batchReport?: ReactNode;
  onApply: (query: AssetListQuery, offset: number) => void;
  onSelect: (id: string) => void;
  onBatch: () => void;
  onOpen: (asset: AssetItem) => void;
  onRights: (asset: AssetItem) => void;
}

export function AssetLibrary(props: LibraryProps) {
  const { t } = useAssetLibraryTranslation();
  const { query, assets, offset, total } = props;
  const [search, setSearch] = useState(query.search ?? "");
  const results = useRef<HTMLElement>(null);
  const focused = useRef(false);
  const scrollOnLoad = useRef(false);
  useLayoutEffect(() => {
    if (props.loading) return;
    if (scrollOnLoad.current) {
      scrollOnLoad.current = false;
      results.current?.scrollIntoView?.({ block: "start" });
      results.current?.focus({ preventScroll: true });
    } else if (!focused.current && props.restoreFocus && assets.length) {
      const row = Array.from(
        results.current?.querySelectorAll<HTMLButtonElement>("[data-library-asset]") ?? [],
      ).find((element) => element.dataset.libraryAsset === props.restoreFocus);
      row?.focus({ preventScroll: true });
      row?.scrollIntoView?.({ block: "center" });
    }
    focused.current = true;
  }, [props.loading, props.restoreFocus, assets]);
  const filtered = Boolean(query.search || query.assetType || query.rightsStatus);
  function filter(changes: Partial<AssetListQuery>) {
    props.onApply({ ...query, search: search.trim() || undefined, ...changes }, 0);
  }
  return (
    <div className="xj-asset-library">
      <details className="xj-lib-guide">
        <summary>
          <CircleHelp size={17} aria-hidden="true" />
          {t("help")}
          <ChevronRight size={16} aria-hidden="true" />
        </summary>
        <p>{t("helpBody")}</p>
        <p>{t("helpAction")}</p>
      </details>
      {props.manual}
      <section
        ref={results}
        tabIndex={-1}
        className="xj-lib-results"
        aria-label={props.scopeTitle}
        aria-busy={props.loading}
      >
        <div className="xj-lib-heading">
          <div>
            <h2>{props.scopeTitle}</h2>
            <p>{t(props.episode ? "episodeTip" : props.project ? "scopeTip" : "globalTip")}</p>
          </div>
          <span role="status" className="xj-lib-count">
            {props.loading ? t("loading") : t("count", { count: total })}
          </span>
        </div>
        <form
          className="xj-lib-filters"
          onSubmit={(event) => {
            event.preventDefault();
            filter({});
          }}
        >
          <label className="xj-lib-search">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">{t("search")}</span>
            <input
              type="search"
              placeholder={t("placeholder")}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label>
            <span className="sr-only">{t("type")}</span>
            <select
              aria-label={t("type")}
              value={query.assetType ?? ""}
              onChange={(event) => filter({ assetType: (event.target.value as AssetType) || undefined })}
            >
              <option value="">{t("allTypes")}</option>
              {(Object.keys(assetKinds) as AssetType[]).map((kind) => (
                <option value={kind} key={kind}>
                  {t(kind)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">{t("status")}</span>
            <select
              aria-label={t("status")}
              value={query.rightsStatus ?? ""}
              onChange={(event) =>
                filter({ rightsStatus: (event.target.value as RightsStatus) || undefined })
              }
            >
              <option value="">{t("allRights")}</option>
              {states.map((state) => (
                <option value={state} key={state}>
                  {t(state)}
                </option>
              ))}
            </select>
          </label>
          <button className="xj-lib-btn" type="submit">
            {t("apply")}
          </button>
          {filtered ? (
            <button
              type="button"
              className="xj-lib-textbtn"
              onClick={() => {
                setSearch("");
                props.onApply({}, 0);
              }}
            >
              {t("clear")}
            </button>
          ) : null}
        </form>
        {!props.manage ? <p className="xj-lib-note">{t("readOnly")}</p> : null}
        {props.selected.length ? (
          <div className="xj-lib-selection">
            <div>
              <strong>{t("selected", { count: props.selected.length })}</strong>
              <p>{t("bulkHint")}</p>
            </div>
            <button
              type="button"
              disabled={props.busy || props.loading}
              className="xj-lib-btn"
              onClick={props.onBatch}
            >
              {t("bulk")}
            </button>
          </div>
        ) : null}
        {assets.length ? (
          <ul className="xj-lib-list">
            {assets.map((asset) => {
              const Icon = assetKinds[asset.type];
              const places = asset.metadata.sourceSceneIds;
              const paragraphs = asset.metadata.sourceParagraphIds;
              const sourceCount = (ids: unknown[]) =>
                new Set(ids.filter((id) => typeof id === "string" && id.trim())).size;
              let description = asset.description?.trim();
              if (!description && Array.isArray(places) && sourceCount(places))
                description = t("inScenes", { count: sourceCount(places) });
              if (!description && Array.isArray(paragraphs) && sourceCount(paragraphs))
                description = t("inParagraphs", { count: sourceCount(paragraphs) });
              const own = !props.ownerProject || !asset.projectId || props.ownerProject === asset.projectId;
              const manage = props.manage && own;
              return (
                <li key={asset.id} className="xj-lib-row">
                  <div className={"xj-lib-art xj-lib-" + asset.type}>
                    {asset.thumbnailUrl ? (
                      <img src={asset.thumbnailUrl} alt="" loading="lazy" />
                    ) : (
                      <Icon size={23} strokeWidth={1.6} aria-hidden="true" />
                    )}
                  </div>
                  <div className="xj-lib-identity">
                    <div className="xj-lib-name">
                      <span>{t(asset.type)}</span>
                      <button
                        data-library-asset={asset.id}
                        type="button"
                        disabled={props.loading}
                        onClick={() => props.onOpen(asset)}
                      >
                        {asset.name}
                      </button>
                    </div>
                    <p title={description}>{description || t("noDescription")}</p>
                  </div>
                  <span className="xj-lib-shots">
                    <Clapperboard size={14} aria-hidden="true" />
                    {asset.referenceCount ? t("shots", { count: asset.referenceCount }) : t("unused")}
                  </span>
                  <div className="xj-lib-permission">
                    <span className={"xj-lib-state is-" + asset.rightsStatus}>{t(asset.rightsStatus)}</span>
                    <button
                      type="button"
                      className="xj-lib-textbtn"
                      disabled={props.loading || props.busy}
                      onClick={() => props.onRights(asset)}
                    >
                      {t(manage ? rightsAction[asset.rightsStatus] : "view")}
                      <ChevronRight size={13} aria-hidden="true" />
                    </button>
                  </div>
                  <div className="xj-lib-commands">
                    <button
                      type="button"
                      className="xj-lib-btn"
                      disabled={props.loading || props.busy}
                      onClick={() => props.onOpen(asset)}
                    >
                      {t(manage ? "edit" : "open")}
                    </button>
                    {props.project && manage && asset.rightsStatus === "pending" ? (
                      <input
                        type="checkbox"
                        aria-label={t("choose", { name: asset.name })}
                        disabled={props.loading || props.busy}
                        checked={props.selected.includes(asset.id)}
                        onChange={() => props.onSelect(asset.id)}
                      />
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        ) : props.loading ? (
          <p className="xj-lib-empty">{t("loading")}</p>
        ) : (
          <div className="xj-lib-empty">
            {filtered ? <SlidersHorizontal aria-hidden="true" /> : <BookOpen aria-hidden="true" />}
            <h3>{t(filtered ? "noMatch" : "none")}</h3>
            <p>{t(filtered ? "noMatchHint" : props.project ? "noneProject" : "noneWorkspace")}</p>
          </div>
        )}
        {total > LIBRARY_PAGE_SIZE ? (
          <Pagination
            label={t("pagination")}
            total={total}
            pageSize={LIBRARY_PAGE_SIZE}
            offset={offset}
            disabled={props.loading || props.busy}
            onOffsetChange={(value) => {
              scrollOnLoad.current = true;
              props.onApply(query, value);
            }}
          />
        ) : null}
      </section>
      {props.batchReport}
    </div>
  );
}

export function RightsSummary({
  asset,
  onOpen,
  disabled,
  editable,
}: {
  asset: AssetItem;
  editable: boolean;
  onOpen: () => void;
  disabled: boolean;
}) {
  const { t } = useAssetLibraryTranslation();
  return (
    <section className="xj-lib-summary">
      <h2>
        <ShieldCheck size={19} aria-hidden="true" />
        {t("status")}
      </h2>
      <span className={"xj-lib-state is-" + asset.rightsStatus}>{t(asset.rightsStatus)}</span>
      <p>{t((asset.rightsStatus + "Help") as "missingHelp")}</p>
      <button type="button" className="xj-lib-btn" onClick={onOpen} disabled={disabled}>
        {t(editable ? rightsAction[asset.rightsStatus] : "view")}
      </button>
      {disabled ? <p>{t("settingsFirst")}</p> : null}
    </section>
  );
}
