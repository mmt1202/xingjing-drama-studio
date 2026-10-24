import i18n from "@/i18n";

const TOKEN_KEY = "arcreel_auth_token";
const REFRESH_TOKEN_KEY = "xingjing_refresh_token";
const ACCESS_EXPIRES_KEY = "xingjing_access_expires_at";
const SESSION_EPOCH_KEY = "xingjing_auth_session_epoch";
const CREDENTIAL_KEY = "xingjing_auth_credentials";
export const AUTH_TOKEN_REFRESHED_EVENT = "xingjing:auth-token-refreshed";
export const AUTH_SESSION_EXPIRED_EVENT = "xingjing:auth-session-expired";
export const AUTH_SESSION_CHANGED_EVENT = "xingjing:auth-session-changed";
const authCopy = {
  zh: { AUTH_SESSION_CHANGED: "登录账号已变更，请在当前账号下重新打开此页面。", AUTH_SCOPE_CHANGED: "当前工作区已变更，请重新确认工作区后继续。", AUTH_REFRESH_UNAVAILABLE: "暂时无法续期登录，输入已保留，请稍后重试。", AUTH_SESSION_EXPIRED: "登录已失效，请重新登录后继续。", AUTH_WRITE_RETRY_REQUIRED: "登录已续期，原操作未自动重发；请核对保留的输入后重试。" },
  en: { AUTH_SESSION_CHANGED: "The signed-in account changed. Reopen this page under the current account.", AUTH_SCOPE_CHANGED: "The current workspace changed. Confirm your workspace before continuing.", AUTH_REFRESH_UNAVAILABLE: "Sign-in cannot be renewed right now. Your input is preserved. Please retry later.", AUTH_SESSION_EXPIRED: "Your session expired. Sign in again to continue.", AUTH_WRITE_RETRY_REQUIRED: "Sign-in was renewed. The original action was not resent. Review the preserved input and retry." },
  vi: { AUTH_SESSION_CHANGED: "Tài khoản đăng nhập đã thay đổi. Mở lại trang bằng tài khoản hiện tại.", AUTH_SCOPE_CHANGED: "Không gian hiện tại đã thay đổi. Xác nhận lại không gian trước khi tiếp tục.", AUTH_REFRESH_UNAVAILABLE: "Hiện chưa thể gia hạn đăng nhập. Đã giữ nội dung nhập. Vui lòng thử lại sau.", AUTH_SESSION_EXPIRED: "Phiên đăng nhập đã hết hạn. Đăng nhập lại để tiếp tục.", AUTH_WRITE_RETRY_REQUIRED: "Đã gia hạn đăng nhập. Thao tác gốc chưa được gửi lại. Kiểm tra nội dung đã giữ rồi thử lại." },
};
for (const [language, copy] of Object.entries(authCopy)) i18n.addResourceBundle(language, "authSession", copy, true, false);
type AuthFailureCode = keyof typeof authCopy.en;
export class AuthSessionError extends Error {
  constructor(readonly code: AuthFailureCode, readonly status = 401) { super(i18n.t("authSession:" + code)); }
}
interface Credentials { epoch: string; accessToken: string | null; refreshToken: string | null; expiresAt?: string }
interface TrustedIdentity { actorId: string; workspaceId: string | null }
let memoryEpoch: string = crypto.randomUUID();
let storedEpochSeen = false;
let memoryCredentials: Credentials | undefined;
let trustedIdentity: { epoch: string; value: TrustedIdentity } | undefined;
let renewedCredential: { epoch: string; token: string } | undefined;
const knownTokens = new Map<string, Set<string>>();
function storageSet(key: string, value: string) { try { localStorage.setItem(key, value); } catch { /* Credentials remain available in memory when browser storage is disabled. */ } }
function storageRemove(key: string) { try { localStorage.removeItem(key); } catch { /* Browser storage can be disabled. */ } }
export function getSessionEpoch(): string {
  try {
    const stored = localStorage.getItem(SESSION_EPOCH_KEY);
    if (stored) { storedEpochSeen = true; memoryEpoch = stored; return stored; }
    if (storedEpochSeen) memoryEpoch = crypto.randomUUID();
    localStorage.setItem(SESSION_EPOCH_KEY, memoryEpoch); storedEpochSeen = true;
    return memoryEpoch;
  } catch { return memoryEpoch; }
}
function credentials(): Credentials {
  const epoch = getSessionEpoch();
  try {
    const raw = localStorage.getItem(CREDENTIAL_KEY);
    if (raw) {
      const value = JSON.parse(raw) as Credentials;
      if (value.epoch !== epoch) return { epoch, accessToken: null, refreshToken: null };
      if ((value.accessToken === null || typeof value.accessToken === "string") && (value.refreshToken === null || typeof value.refreshToken === "string")) return value;
      return { epoch, accessToken: null, refreshToken: null };
    }
    return { epoch, accessToken: localStorage.getItem(TOKEN_KEY), refreshToken: localStorage.getItem(REFRESH_TOKEN_KEY), expiresAt: localStorage.getItem(ACCESS_EXPIRES_KEY) ?? undefined };
  } catch { return memoryCredentials?.epoch === epoch ? memoryCredentials : { epoch, accessToken: null, refreshToken: null }; }
}
function publish(name: string, epoch: string) {
  globalThis.dispatchEvent(new CustomEvent(name, { detail: { sessionEpoch: epoch, accessToken: getToken() } }));
}
function rememberToken(epoch: string, token: string | null) {
  if (!token) return;
  const tokens = knownTokens.get(epoch) ?? new Set<string>(); tokens.add(token); knownTokens.set(epoch, tokens);
}
function writeCredentials(value: Credentials) {
  memoryCredentials = value; storageSet(CREDENTIAL_KEY, JSON.stringify(value));
  if (value.accessToken) storageSet(TOKEN_KEY, value.accessToken); else storageRemove(TOKEN_KEY);
  if (value.refreshToken) storageSet(REFRESH_TOKEN_KEY, value.refreshToken); else storageRemove(REFRESH_TOKEN_KEY);
  if (value.expiresAt) storageSet(ACCESS_EXPIRES_KEY, value.expiresAt); else storageRemove(ACCESS_EXPIRES_KEY);
  rememberToken(value.epoch, value.accessToken);
}
export function getToken(): string | null { return credentials().accessToken; }
export function getRefreshToken(): string | null { return credentials().refreshToken; }
/** A deliberate login creates a new identity boundary; token renewal does not. */
export function startAuthSession(token: string, refreshToken?: string, expiresAt?: string): string {
  const epoch = crypto.randomUUID(); memoryEpoch = epoch; trustedIdentity = undefined; renewedCredential = undefined; knownTokens.clear();
  storageSet(SESSION_EPOCH_KEY, epoch);
  writeCredentials({ epoch, accessToken: token, refreshToken: refreshToken ?? null, expiresAt });
  publish(AUTH_SESSION_CHANGED_EVENT, epoch); return epoch;
}
export function setToken(token: string, expiresAt?: string): void { startAuthSession(token, undefined, expiresAt); }
export function setRefreshToken(token: string): void { writeCredentials({ ...credentials(), refreshToken: token }); }
export function clearToken(expectedEpoch = getSessionEpoch()): boolean {
  if (getSessionEpoch() !== expectedEpoch) return false;
  const epoch = crypto.randomUUID(); memoryEpoch = epoch; trustedIdentity = undefined; renewedCredential = undefined; knownTokens.clear();
  storageSet(SESSION_EPOCH_KEY, epoch);
  writeCredentials({ epoch, accessToken: null, refreshToken: null });
  publish(AUTH_SESSION_EXPIRED_EVENT, epoch); return true;
}
interface RefreshFlight { epoch: string; refreshToken: string; promise: Promise<string | null> }
let refreshFlight: RefreshFlight | undefined;
function assertEpoch(epoch: string, signal?: AbortSignal | null) {
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (getSessionEpoch() !== epoch) throw new AuthSessionError("AUTH_SESSION_CHANGED", 409);
}
export function getTokenForSession(expectedEpoch: string): string | null { assertEpoch(expectedEpoch); return getToken(); }
async function readAuthDocument(path: string, init: RequestInit, fetcher: typeof fetch = fetch): Promise<{ response: Response; body: unknown }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetcher(path, { ...init, signal: controller.signal });
    let body: unknown;
    try { body = await response.json(); } catch (error) { if (controller.signal.aborted) throw error; body = null; }
    return { response, body };
  } catch { throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 503); }
  finally { clearTimeout(timeout); }
}
export function refreshAccessToken(expectedEpoch = getSessionEpoch()): Promise<string | null> {
  try { assertEpoch(expectedEpoch); } catch (error) { return Promise.reject(error instanceof Error ? error : new AuthSessionError("AUTH_SESSION_CHANGED", 409)); }
  const snapshot = credentials();
  if (!snapshot.refreshToken) return Promise.resolve(null);
  if (refreshFlight?.epoch === expectedEpoch && refreshFlight.refreshToken === snapshot.refreshToken) return refreshFlight.promise;
  const flight: RefreshFlight = { epoch: expectedEpoch, refreshToken: snapshot.refreshToken, promise: Promise.resolve(null) };
  const run = async (serialized: boolean) => {
    assertEpoch(expectedEpoch);
    const before = credentials();
    if (before.refreshToken !== snapshot.refreshToken || before.accessToken !== snapshot.accessToken) return before.accessToken;
    let document: { response: Response; body: unknown };
    try { document = await readAuthDocument("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refreshToken: snapshot.refreshToken }) }); }
    catch { assertEpoch(expectedEpoch); throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 503); }
    const { response, body: envelope } = document;
    assertEpoch(expectedEpoch);
    const current = credentials();
    if (current.refreshToken !== snapshot.refreshToken || current.accessToken !== snapshot.accessToken) return current.accessToken;
    if (!response.ok) {
      if ([400, 401, 403].includes(response.status)) {
        if (serialized) { clearToken(expectedEpoch); return null; }
        // Without a cross-tab lock, another tab may still be committing a rotated token.
        throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 503);
      }
      throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", response.status);
    }
    assertEpoch(expectedEpoch);
    const latest = credentials();
    if (latest.refreshToken !== snapshot.refreshToken || latest.accessToken !== snapshot.accessToken) return latest.accessToken;
    const value = (envelope as { data?: { accessToken?: unknown; refreshToken?: unknown; accessExpiresAt?: unknown } } | null)?.data;
    if (!value || typeof value.accessToken !== "string" || !value.accessToken || typeof value.refreshToken !== "string" || !value.refreshToken || typeof value.accessExpiresAt !== "string" || !Number.isFinite(Date.parse(value.accessExpiresAt))) throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 502);
    writeCredentials({ epoch: expectedEpoch, accessToken: value.accessToken, refreshToken: value.refreshToken, expiresAt: value.accessExpiresAt });
    renewedCredential = { epoch: expectedEpoch, token: value.accessToken };
    publish(AUTH_TOKEN_REFRESHED_EVENT, expectedEpoch); return value.accessToken;
  };
  flight.promise = Promise.resolve().then(async () => {
    if (typeof navigator === "undefined" || !navigator.locks) return run(false);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25_000);
    try { return await navigator.locks.request("xingjing.auth.refresh", { mode: "exclusive", signal: controller.signal }, () => run(true)); }
    catch (error) { if (error instanceof AuthSessionError) throw error; assertEpoch(expectedEpoch); throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 503); }
    finally { clearTimeout(timeout); }
  }).finally(() => { if (refreshFlight === flight) refreshFlight = undefined; });
  refreshFlight = flight; return flight.promise;
}
function parseIdentity(body: unknown): TrustedIdentity {
  const value = (body as { data?: { user?: { id?: unknown }; currentWorkspace?: { id?: unknown } | null } } | null)?.data;
  if (!value || typeof value.user?.id !== "string" || !value.user.id || value.currentWorkspace !== null && typeof value.currentWorkspace?.id !== "string") throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 502);
  return { actorId: value.user.id, workspaceId: (value.currentWorkspace?.id as string | undefined) ?? null };
}
let contextFlight: { epoch: string; token: string; promise: Promise<TrustedIdentity> } | undefined;
async function verifyIdentity(epoch: string, token: string, fetcher: typeof fetch): Promise<TrustedIdentity> {
  if (contextFlight?.epoch === epoch && contextFlight.token === token) return contextFlight.promise;
  const promise = (async () => {
    let document: { response: Response; body: unknown };
    try { document = await readAuthDocument("/api/v1/session/context", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json", Authorization: "Bearer " + token } }, fetcher); }
    catch { assertEpoch(epoch); throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", 503); }
    const { response, body } = document;
    assertEpoch(epoch);
    if (!response.ok) {
      if ([401, 403].includes(response.status) && getToken() === token) { clearToken(epoch); throw new AuthSessionError("AUTH_SESSION_EXPIRED"); }
      throw new AuthSessionError("AUTH_REFRESH_UNAVAILABLE", response.status);
    }
    const value = parseIdentity(body); assertEpoch(epoch);
    if (trustedIdentity?.epoch === epoch && trustedIdentity.value.actorId !== value.actorId) { clearToken(epoch); throw new AuthSessionError("AUTH_SESSION_CHANGED", 409); }
    trustedIdentity = { epoch, value };
    if (renewedCredential?.epoch === epoch && renewedCredential.token === token) renewedCredential = undefined;
    return value;
  })();
  contextFlight = { epoch, token, promise };
  try { return await promise; } finally { if (contextFlight?.promise === promise) contextFlight = undefined; }
}
function guardResponse(response: Response, epoch: string, signal?: AbortSignal | null): Response {
  return new Proxy(response, { get(target, property) {
    assertEpoch(epoch, signal);
    const value: unknown = Reflect.get(target, property, target);
    if (property === "clone") return () => guardResponse(target.clone(), epoch, signal);
    if (["json", "text", "blob", "arrayBuffer", "formData", "bytes"].includes(String(property)) && typeof value === "function") return async (...args: unknown[]) => { assertEpoch(epoch, signal); const result: unknown = await Reflect.apply(value, target, args); assertEpoch(epoch, signal); return result; };
    return typeof value === "function" ? value.bind(target) as unknown : value;
  } });
}
/** Bind a client to one login boundary while allowing that same session to renew. */
export function createAuthenticatedFetcher(options: { workspaceId?: string; actorId?: string; fetcher?: typeof fetch } = {}): typeof fetch {
  const epoch = getSessionEpoch();
  const initialToken = getToken(); rememberToken(epoch, initialToken);
  const expectedActor = options.actorId ?? (trustedIdentity?.epoch === epoch ? trustedIdentity.value.actorId : undefined);
  const fetcher = options.fetcher ?? fetch;
  return async (input, init = {}) => {
    assertEpoch(epoch, init.signal);
    const requestInput = typeof Request !== "undefined" && input instanceof Request ? input : undefined;
    const signal = init.signal ?? requestInput?.signal;
    const origin = globalThis.location?.origin ?? "http://localhost";
    const url = new URL(input instanceof URL ? input.href : typeof input === "string" ? input : input.url, origin);
    if (url.origin !== origin || !url.pathname.startsWith("/api/v1/")) {
      const response = await fetcher(input, init); assertEpoch(epoch, signal); return guardResponse(response, epoch, signal);
    }
    const suppliedHeaders = new Headers(init.headers ?? requestInput?.headers);
    const suppliedToken = suppliedHeaders.get("Authorization")?.replace(/^Bearer\s+/i, "");
    const currentToken = getToken();
    const managed = Boolean(currentToken) && (!suppliedToken || suppliedToken === initialToken || suppliedToken === currentToken || Boolean(knownTokens.get(epoch)?.has(suppliedToken)));
    const method = (init.method ?? requestInput?.method ?? "GET").toUpperCase();
    const expectedWorkspace = options.workspaceId ?? suppliedHeaders.get("X-Workspace-Id") ?? undefined;
    const expectedActorId = options.actorId ?? suppliedHeaders.get("X-Expected-Actor-Id") ?? expectedActor;
    const checkIdentity = (identity: TrustedIdentity) => {
      if (expectedActorId && identity.actorId !== expectedActorId) { clearToken(epoch); throw new AuthSessionError("AUTH_SESSION_CHANGED", 409); }
      if (expectedWorkspace && identity.workspaceId !== expectedWorkspace) throw new AuthSessionError("AUTH_SCOPE_CHANGED", 409);
    };
    if (managed && renewedCredential?.epoch === epoch && renewedCredential.token === currentToken && url.pathname !== "/api/v1/session/context") {
      const identity = await verifyIdentity(epoch, currentToken, fetcher); assertEpoch(epoch, signal); checkIdentity(identity);
    }
    const request = async (token: string | null) => {
      assertEpoch(epoch, signal);
      const headers = new Headers(suppliedHeaders);
      if (managed && token) headers.set("Authorization", "Bearer " + token);
      if (options.workspaceId) headers.set("X-Workspace-Id", options.workspaceId);
      if (options.actorId) headers.set("X-Expected-Actor-Id", options.actorId);
      const original = init.headers;
      const outgoing = original && !Array.isArray(original) && !(original instanceof Headers)
        ? Object.fromEntries([...headers.entries()].map(([name, value]) => [Object.keys(original).find((key) => key.toLowerCase() === name) ?? name, value])) : headers;
      const response = await fetcher(input, { ...init, headers: outgoing }); assertEpoch(epoch, signal); return response;
    };
    let response = await request(currentToken);
    if (response.status === 401 && managed) {
      let renewed = getToken();
      if (!renewed || renewed === currentToken) renewed = await refreshAccessToken(epoch);
      if (!renewed) {
        if (getSessionEpoch() === epoch) clearToken(epoch);
        throw new AuthSessionError("AUTH_SESSION_EXPIRED");
      }
      assertEpoch(epoch, signal);
      const identity = await verifyIdentity(epoch, renewed, fetcher); assertEpoch(epoch, signal);
      checkIdentity(identity);
      if (method !== "GET" && method !== "HEAD") throw new AuthSessionError("AUTH_WRITE_RETRY_REQUIRED");
      response = await request(renewed);
      if (response.status === 401) { if (getToken() === renewed) clearToken(epoch); throw new AuthSessionError("AUTH_SESSION_EXPIRED"); }
    }
    if (managed && response.ok && ["/api/v1/session/context", "/api/v1/session/context/workspace"].includes(url.pathname)) {
      const value = parseIdentity(await response.clone().json()); assertEpoch(epoch, signal);
      checkIdentity(value);
      trustedIdentity = { epoch, value };
      if (renewedCredential?.epoch === epoch && renewedCredential.token === getToken()) renewedCredential = undefined;
    }
    return guardResponse(response, epoch, signal);
  };
}
export function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> { return createAuthenticatedFetcher()(input, init); }
export function getAuthHeader(): string | null { const token = getToken(); return token ? "Bearer " + token : null; }
export function isAuthStorageKey(key: string | null): boolean { return key === null || [TOKEN_KEY, REFRESH_TOKEN_KEY, ACCESS_EXPIRES_KEY, SESSION_EPOCH_KEY, CREDENTIAL_KEY].includes(key); }
