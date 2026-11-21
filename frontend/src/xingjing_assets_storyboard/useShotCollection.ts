import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AssetsStoryboardApiError } from "./api";
import type { AssetsStoryboardPort, PageResult, SessionContext, ShotItem, WorkspaceProjectScope } from "./contracts";
import type { StoryboardView } from "./StoryboardWorkspace";

export const SHOTS_PER_PAGE = 10;
type Position = { boardId?: string; offset: number };
function readPosition(key: string, preferred?: string): Position {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (saved && typeof saved === "object" && "boardId" in saved && typeof saved.boardId === "string" &&
        (!preferred || preferred === saved.boardId) && "offset" in saved && typeof saved.offset === "number" &&
        Number.isSafeInteger(saved.offset) && saved.offset >= 0 && saved.offset % SHOTS_PER_PAGE === 0)
      return { boardId: saved.boardId, offset: saved.offset };
  } catch { /* A private browser session can still page in memory. */ }
  return { boardId: preferred, offset: 0 };
}
function remember(key: string, position: Position) {
  try { sessionStorage.setItem(key, JSON.stringify(position)); } catch { /* Storage is optional. */ }
}

export function useShotCollection(api: AssetsStoryboardPort, scope: WorkspaceProjectScope, view: StoryboardView, initialBoardId?: string) {
  const { workspaceId, projectId, episodeId } = scope;
  const stableScope = useMemo(() => ({ workspaceId, projectId, episodeId }), [workspaceId, projectId, episodeId]);
  const storageKey = "shot-list:" + JSON.stringify([workspaceId, projectId, episodeId]);
  const initial = useMemo(() => readPosition(storageKey, initialBoardId), [storageKey, initialBoardId]);
  const [position, setPosition] = useState<{ storageKey: string; initial: Position; value: Position }>();
  const desired = position?.storageKey === storageKey && position.initial === initial ? position.value : initial;
  const paged = view === "list" || view === "batch" || view === "storyboard";
  const offset = paged ? desired.offset : 0;
  const requestKey = JSON.stringify([storageKey, view, desired.boardId, offset]);
  const active = useRef<AbortController | null>(null);
  const [response, setResponse] = useState<{ key: string; page: PageResult<ShotItem>; context: SessionContext }>();
  const [failure, setFailure] = useState<{ key: string; cause: unknown }>();
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (view === "smart") return;
    active.current?.abort();
    const abort = new AbortController();
    active.current = abort;
    setLoading(true); setFailure(undefined);
    try {
      const context = await api.getSessionContext(stableScope, abort.signal);
      if (!context.permissions.includes("shot.view")) throw new AssetsStoryboardApiError(403, "PERMISSION_DENIED", "PERMISSION_DENIED");
      const query = { browseStoryboard: true, storyboardId: desired.boardId, pageSize: paged ? SHOTS_PER_PAGE : 200, includeQualityPolicy: true };
      let page = await api.listShots(stableScope, { ...query, pageToken: String(offset) }, abort.signal);
      if (!paged && page.nextToken) {
        const items = [...page.items], seen = new Set<string>();
        let token = page.nextToken;
        while (token && !abort.signal.aborted) {
          if (seen.has(token)) throw new Error("INVALID_PAGE_TOKEN");
          seen.add(token);
          const more = await api.listShots(stableScope, { ...query, storyboardId: page.shotList?.boardId ?? desired.boardId, pageToken: token }, abort.signal);
          if (!more.items.length || more.collectionVersion !== page.collectionVersion) throw new Error("VERSION_CONFLICT");
          items.push(...more.items); token = more.nextToken ?? "";
        }
        page = { ...page, items, nextToken: null };
      }
      if (abort.signal.aborted) return;
      const boardId = page.shotList?.boardId ?? page.items[0]?.storyboardId;
      if (boardId) remember(storageKey, { boardId, offset: paged ? page.shotList?.offset ?? offset : desired.offset });
      setResponse({ key: requestKey, page, context });
    } catch (cause) {
      if (!abort.signal.aborted) { setFailure({ key: requestKey, cause }); setResponse(undefined); }
    } finally { if (!abort.signal.aborted) setLoading(false); }
  }, [api, stableScope, view, desired.boardId, desired.offset, paged, offset, requestKey, storageKey]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      active.current?.abort();
    };
  }, [load]);
  const result = response?.key === requestKey ? response : undefined;
  const page = result?.page;
  const boardId = page?.shotList?.boardId ?? page?.items[0]?.storyboardId;
  function visit(board: string | undefined, next: number) {
    const value = { boardId: board, offset: next };
    remember(storageKey, value);
    setPosition({ storageKey, initial, value });
  }
  return { context: result?.context, page, boardId, load, loading,
    error: failure?.key === requestKey ? failure.cause : undefined,
    total: page?.total ?? page?.items.length ?? 0, offset: page?.shotList?.offset ?? offset,
    order: page?.shotList?.order ?? page?.items.map(item => item.id) ?? [],
    chooseBoard: (board: string) => visit(board, 0), turnPage: (next: number) => visit(boardId, next) };
}
