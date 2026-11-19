import { useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCheck,
  ChevronDown,
  Combine,
  Scissors,
  SlidersHorizontal,
} from "lucide-react";
import { Pagination } from "@/components/ui/Pagination";
import type { AssetsStoryboardPort, ShotItem, SmartEpisodeInputs, WorkspaceProjectScope } from "./contracts";
import { AssetsStoryboardApiError } from "./api";
import { useDraftProtection } from "./useDraftProtection";
import { useSmartShotsTranslation } from "./smart-i18n";
import type { SmartNavigation } from "./SmartStoryboardWorkspace";

const pageSize = 12;
export function SmartShotDraft({
  scope,
  api,
  input,
  onOpen,
  onChange,
  refreshing,
  initialBoardId,
}: {
  scope: WorkspaceProjectScope;
  api: AssetsStoryboardPort;
  input: SmartEpisodeInputs;
  onOpen?: SmartNavigation;
  onChange: () => void;
  refreshing: boolean;
  initialBoardId?: string;
}) {
  const { t, i18n } = useSmartShotsTranslation();
  const [choice, setChoice] = useState(initialBoardId);
  const board = input.boards.find((b) => b.id === choice) ?? input.boards[0];
  const [position, setPosition] = useState(0);
  const offset = Math.min(position, Math.max(0, Math.floor((board.shots - 1) / pageSize) * pageSize));
  const [revision, reload] = useState(0),
    [selected, setSelected] = useState<string[]>([]),
    [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string>();
  const key = JSON.stringify([
    scope.workspaceId,
    scope.projectId,
    scope.episodeId,
    board.id,
    board.version,
    offset,
    revision,
  ]);
  const [read, setRead] = useState<{ key: string; shots?: readonly ShotItem[]; error?: boolean }>();
  const locked = useRef(false);
  useDraftProtection(busy);
  useEffect(() => {
    const ctl = new AbortController();
    void api
      .listShots(
        { workspaceId: scope.workspaceId, projectId: scope.projectId, episodeId: scope.episodeId },
        { storyboardId: board.id, pageSize, pageToken: String(offset) },
        ctl.signal,
      )
      .then(
        (page) => {
          if (!ctl.signal.aborted) setRead({ key, shots: page.items });
        },
        () => {
          if (!ctl.signal.aborted) setRead({ key, error: true });
        },
      );
    return () => ctl.abort();
  }, [api, key, scope.workspaceId, scope.projectId, scope.episodeId, board.id, offset]);
  const current = read?.key === key ? read : undefined,
    rows = current?.shots ?? [];
  const picked = rows.filter((s) => selected.includes(s.id));
  const indexes = picked.map((s) => rows.indexOf(s));
  const contiguous = indexes.every((index, i) => i === 0 || index === indexes[i - 1] + 1);
  const editable = input.canManage && !board.frozen && !busy && !refreshing && Boolean(current?.shots);
  function page(n: number) {
    setPosition(n);
    setSelected([]);
  }
  async function edit(action: "splitShot" | "mergeShots" | "reorder", target: ShotItem, direction = 0) {
    if (locked.current || !editable) return;
    locked.current = true;
    setBusy(true);
    setNotice(undefined);
    try {
      let payload: Record<string, unknown> = {};
      if (action === "mergeShots") payload = { selectedShotIds: picked.map((s) => s.id) };
      if (action === "reorder") {
        const order: string[] = [];
        let token: string | undefined;
        do {
          const pageResult = await api.listShots(scope, {
            storyboardId: board.id,
            pageToken: token,
            pageSize: 200,
          });
          if (
            pageResult.items.some(
              (s) =>
                s.storyboardId !== board.id ||
                s.episodeId !== scope.episodeId ||
                s.version !== target.version,
            )
          )
            throw new Error("orderChanged");
          order.push(...pageResult.items.map((s) => s.id));
          const next = pageResult.nextToken ?? undefined;
          if (next && (!pageResult.items.length || next === token || order.length >= board.shots))
            throw new Error("orderChanged");
          token = next;
        } while (token);
        const from = order.indexOf(target.id),
          to = from + direction;
        if (
          from < 0 ||
          to < 0 ||
          to >= order.length ||
          order.length !== board.shots ||
          new Set(order).size !== order.length
        )
          throw new Error("orderChanged");
        [order[from], order[to]] = [order[to], order[from]];
        payload = { orderedShotIds: order };
      }
      await api.shotAction(scope, { action, targetId: target.id, version: target.version, payload });
      setSelected([]);
      setNotice("saved");
      onChange();
    } catch (e) {
      setNotice(
        (e instanceof AssetsStoryboardApiError && e.status === 409) ||
          (e instanceof Error && e.message === "orderChanged")
          ? "conflict"
          : "actionFailed",
      );
      onChange();
    } finally {
      locked.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="ss-panel" aria-label={t("board")}>
      <div className="ss-heading">
        <div>
          <h2>{t("board")}</h2>
          <p>
            {t("boardMeta", { count: board.shots, version: board.version })}
            {board.frozen ? " · " + t("frozen") : ""}
          </p>
        </div>
        <div className="ss-tools">
          {onOpen && (
            <>
              <button className="ss-btn" disabled={busy} onClick={() => onOpen("shots", input.number)}>
                {t("shotList")}
              </button>
              <button
                className="ss-btn"
                disabled={busy}
                onClick={() => onOpen("shots-quality", input.number)}
              >
                <CheckCheck size={15} />
                {t("quality")}
              </button>
              <button className="ss-btn" disabled={busy} onClick={() => onOpen("shots-batch", input.number)}>
                <SlidersHorizontal size={15} />
                {t("batch")}
              </button>
              <button className="ss-btn" disabled={busy} onClick={() => onOpen("storyboard", input.number)}>
                {t("boardPage")}
                <ArrowRight size={15} />
              </button>
            </>
          )}
        </div>
      </div>
      {input.boards.length > 1 && (
        <label className="ss-group">
          {t("boardChoice")}{" "}
          <select
            value={board.id}
            disabled={busy}
            onChange={(e) => {
              setChoice(e.target.value);
              page(0);
              setNotice(undefined);
            }}
          >
            {input.boards.map((b, i) => (
              <option key={b.id} value={b.id}>
                {t("boardOption", { number: i + 1, count: b.shots })}
              </option>
            ))}
          </select>
        </label>
      )}
      {notice && (
        <p role="status" className={notice === "saved" ? "ss-success" : "ss-alert"}>
          {t(notice)}
        </p>
      )}
      {input.canManage && !board.frozen && rows.length > 0 && (
        <div className="ss-editbar">
          <span>{t("selected", { count: picked.length })}</span>
          <button
            className="ss-btn"
            disabled={!editable || picked.length !== 1}
            onClick={() => void edit("splitShot", picked[0])}
          >
            <Scissors size={14} />
            {t("split")}
          </button>
          <button
            className="ss-btn"
            disabled={!editable || picked.length < 2 || !contiguous}
            onClick={() => void edit("mergeShots", picked[0])}
          >
            <Combine size={14} />
            {t("merge")}
          </button>
          {picked.length > 0 && (
            <button className="ss-link" onClick={() => setSelected([])}>
              {t("undoSelection")}
            </button>
          )}
          {!contiguous && <span>{t("adjacent")}</span>}
        </div>
      )}
      {busy && (
        <p role="status" className="ss-note">
          {t("writing")}
        </p>
      )}
      {!current ? (
        <p role="status" className="ss-empty">
          {t("loading")}
        </p>
      ) : current.error ? (
        <p role="alert" className="ss-alert">
          {t("boardReadFailed")}{" "}
          <button className="ss-link" onClick={() => reload((n) => n + 1)}>
            {t("retry")}
          </button>
        </p>
      ) : !rows.length ? (
        <p className="ss-empty">{t("noShots")}</p>
      ) : (
        <ol className="ss-shots">
          {rows.map((s, i) => (
            <li key={s.id} className={selected.includes(s.id) ? "ss-picked" : ""}>
              <div className="ss-shot-no">
                {input.canManage && !board.frozen && (
                  <input
                    type="checkbox"
                    aria-label={t("selectShot", { number: s.shotNo })}
                    disabled={!editable}
                    checked={selected.includes(s.id)}
                    onChange={() =>
                      setSelected((prev) =>
                        prev.includes(s.id) ? prev.filter((id) => id !== s.id) : [...prev, s.id],
                      )
                    }
                  />
                )}
                <b>{s.shotNo}</b>
              </div>
              <div className="ss-shot-body">
                <div className="ss-shot-line">
                  <strong>
                    {[s.shotSize, s.cameraMove].filter(Boolean).join(" · ") ||
                      t("shot", { number: s.shotNo })}
                  </strong>
                  <span>
                    {t("seconds", {
                      value: new Intl.NumberFormat(i18n.resolvedLanguage ?? "zh", {
                        maximumFractionDigits: 2,
                      }).format(s.durationMs / 1000),
                    })}
                  </span>
                </div>
                <p>{s.dialogue || s.prompt || t("noDescription")}</p>
                {s.assetReferences.some((ref) => input.assetNames[ref.id] || ref.name !== ref.id) && (
                  <small>
                    {s.assetReferences
                      .map((ref) => input.assetNames[ref.id] ?? (ref.name !== ref.id ? ref.name : ""))
                      .filter(Boolean)
                      .join(" / ")}
                  </small>
                )}
                {s.dialogue && s.prompt && (
                  <details>
                    <summary>
                      {t("more")} <ChevronDown size={12} />
                    </summary>
                    <p>{s.prompt}</p>
                  </details>
                )}
              </div>
              {input.canManage && !board.frozen && (
                <div className="ss-move">
                  <button
                    className="ss-btn"
                    disabled={!editable || offset + i === 0}
                    aria-label={t("up", { number: s.shotNo })}
                    onClick={() => void edit("reorder", s, -1)}
                  >
                    <ArrowUp size={15} />
                  </button>
                  <button
                    className="ss-btn"
                    disabled={!editable || offset + i === board.shots - 1}
                    aria-label={t("down", { number: s.shotNo })}
                    onClick={() => void edit("reorder", s, 1)}
                  >
                    <ArrowDown size={15} />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
      {board.shots > pageSize && (
        <Pagination
          label={t("shotPage")}
          offset={offset}
          pageSize={pageSize}
          total={board.shots}
          disabled={busy}
          onOffsetChange={page}
        />
      )}
    </section>
  );
}
