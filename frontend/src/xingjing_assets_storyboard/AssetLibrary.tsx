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
import type { AssetItem, AssetListQuery, AssetsStoryboardPort, AssetType, BatchReceipt, RightsStatus, WorkspaceProjectScope } from "./contracts";
import { BatchResultPanel } from "./ui";
import { AssetBatchDialog } from "./AssetBatchDialog";
import { batchEligible, type AssetBatchMode } from "./asset-batch";
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
  api: AssetsStoryboardPort;
  scope: WorkspaceProjectScope;
  actorId: string;
  onRefresh: () => void;
  onConfirm: (action: "confirmAssets" | "retryFailedItems", payload: Record<string, unknown>) => Promise<BatchReceipt | undefined>;
  manual?: ReactNode;
  onApply: (query: AssetListQuery, offset: number) => void;
  onOpen: (asset: AssetItem) => void;
  onRights: (asset: AssetItem) => void;
}

export function AssetLibrary(props: LibraryProps) {
  const { t } = useAssetLibraryTranslation();
  const { query, assets, offset, total } = props;
  const [search, setSearch] = useState(query.search ?? "");
  const [selected, setSelected] = useState<readonly AssetItem[]>([]);
  const [mode, setMode] = useState<AssetBatchMode>();
  const [report, setReport] = useState<{ receipt: BatchReceipt; assets: readonly AssetItem[] }>();
  const pending = selected.filter((asset) => asset.rightsStatus === "pending");
  const projectConfirmation = props.project && pending.length > 0 && pending.length <= 100;
  const manageable = (asset: AssetItem) => props.manage && Boolean(props.ownerProject || asset.projectId)
    && (!props.ownerProject || !asset.projectId || props.ownerProject === asset.projectId);
  const page = assets.filter(manageable);
  const selectedIds = new Set(selected.map((asset) => asset.id));
  const pageSelected = page.length > 0 && page.every((asset) => selectedIds.has(asset.id));
  const changeSelection = (items: readonly AssetItem[], add: boolean) => {
    const ids = new Set(items.map((asset) => asset.id));
    setSelected((previous) => add
      ? [...previous.filter((asset) => !ids.has(asset.id)), ...items]
      : previous.filter((asset) => !ids.has(asset.id)));
  };
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
    setSelected([]);
    setReport(undefined);
    props.onApply({ ...query, search: search.trim() || undefined, ...changes }, 0);
  }
  async function confirm(items: readonly AssetItem[], previousOperationId?: string) {
    const records = Object.fromEntries(items.filter((asset) => asset.rights?.id)
      .map((asset) => [asset.id, { version: asset.version, rightsId: asset.rights!.id }]));
    const receipt = await props.onConfirm(previousOperationId ? "retryFailedItems" : "confirmAssets", {
      itemIds: items.map((asset) => asset.id),
      ...(Object.keys(records).length ? { records } : {}),
      ...(previousOperationId ? { previousOperationId } : {}),
    });
    if (!receipt) return;
    const names = new Map(items.map((asset) => [asset.id, asset.name]));
    const succeeded = new Set(receipt.items.filter((item) => item.status === "succeeded").map((item) => item.id));
    setSelected((current) => current.filter((asset) => !succeeded.has(asset.id)));
    setReport({ assets: items, receipt: { ...receipt, items: receipt.items.map((item) => ({
      ...item, name: item.name ?? names.get(item.id) ?? t("assetLabel"),
      message: item.message && /^[A-Z_]+$/.test(item.message)
        ? t(["RIGHTS_SELECTION_CHANGED", "VERSION_CONFLICT"].includes(item.message) ? "conflict" : "proofError") : item.message,
    })) } });
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
                setSelected([]);
                setReport(undefined);
                props.onApply({}, 0);
              }}
            >
              {t("clear")}
            </button>
          ) : null}
        </form>
        {!props.manage ? <p className="xj-lib-note">{t("readOnly")}</p> : null}
        {props.manage && (page.length > 0 || selected.length > 0) ? (
          <div className="xj-lib-selection">
            <label className="xj-batch-select">
              <input type="checkbox" aria-label={t("pageSelect")} checked={pageSelected}
                ref={(node) => { if (node) node.indeterminate = !pageSelected && page.some((item) => selectedIds.has(item.id)); }}
                disabled={props.busy || props.loading || !page.length} onChange={() => changeSelection(page, !pageSelected)} />
              {t("pageSelect")}
            </label>
            <span aria-live="polite">{t("selected", { count: selected.length })}</span>
            {selected.length > 0 && <button type="button" className="xj-lib-textbtn" onClick={() => setSelected([])}>{t("clearSelection")}</button>}
            <div className="xj-batch-buttons">
              {(["registerMany", "reviewMany", "imageMany"] as const).filter((action) => action !== "imageMany" || props.project).map((action) => (
                <button type="button" key={action} className="xj-lib-btn" disabled={props.busy || props.loading ||
                  !(action === "reviewMany" && projectConfirmation) && !selected.some((asset) => batchEligible(action, asset))}
                  onClick={() => { if (action === "reviewMany" && projectConfirmation) void confirm(pending); else setMode(action); }}>
                  {t(action === "reviewMany" && props.project ? "bulk" : action)}</button>
              ))}
            </div>
            {projectConfirmation && <p className="xj-lib-note">{t("bulkHint", { count: pending.length })}</p>}
          </div>
        ) : null}
        {report && <BatchResultPanel hideOperationId receipt={report.receipt} busy={props.busy}
          onRetry={(ids) => void confirm(report.assets.filter((asset) => ids.includes(asset.id)), report.receipt.operationId)} />}
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
                    {manageable(asset) ? (
                      <input
                        type="checkbox"
                        aria-label={t("choose", { name: asset.name })}
                        disabled={props.loading || props.busy}
                        checked={selectedIds.has(asset.id)}
                        onChange={() => changeSelection([asset], !selectedIds.has(asset.id))}
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
      {mode && <AssetBatchDialog key={mode} mode={mode} assets={selected} scope={props.scope} actorId={props.actorId} api={props.api}
        onClose={(updated) => {
          setMode(undefined);
          if (updated.length) {
            const byId = new Map(updated.map((asset) => [asset.id, asset]));
            setSelected((items) => items.map((asset) => byId.get(asset.id) ?? asset));
            props.onRefresh();
          }
        }} />}
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
