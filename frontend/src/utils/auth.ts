const TOKEN_KEY = "arcreel_auth_token";
const REFRESH_TOKEN_KEY = "xingjing_refresh_token";
const ACCESS_EXPIRES_KEY = "xingjing_access_expires_at";
export const AUTH_TOKEN_REFRESHED_EVENT = "xingjing:auth-token-refreshed";
export const AUTH_SESSION_EXPIRED_EVENT = "xingjing:auth-session-expired";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string, expiresAt?: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  if (expiresAt) localStorage.setItem(ACCESS_EXPIRES_KEY, expiresAt);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setRefreshToken(token: string): void {
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(ACCESS_EXPIRES_KEY);
}

let refreshPromise: Promise<string | null> | undefined;

export function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise;
  const refreshToken = getRefreshToken();
  if (!refreshToken) return Promise.resolve(null);
  refreshPromise = fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refreshToken }),
  }).then(async (response) => {
    if (!response.ok) {
      clearToken();
      return null;
    }
    const envelope = await response.json() as { data: {
      accessToken: string;
      refreshToken: string;
      accessExpiresAt: string;
    } };
    setToken(envelope.data.accessToken, envelope.data.accessExpiresAt);
    setRefreshToken(envelope.data.refreshToken);
    globalThis.dispatchEvent(new CustomEvent(AUTH_TOKEN_REFRESHED_EVENT, {
      detail: { accessToken: envelope.data.accessToken },
    }));
    return envelope.data.accessToken;
  }).catch(() => null).finally(() => { refreshPromise = undefined; });
  return refreshPromise;
}

export async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const request = (accessToken: string | null) => {
    const headers = new Headers(init.headers);
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(input, { ...init, headers });
  };
  let response = await request(getToken());
  if (response.status !== 401) return response;
  const refreshedToken = await refreshAccessToken();
  if (refreshedToken) response = await request(refreshedToken);
  if (response.status === 401) {
    clearToken();
    globalThis.dispatchEvent(new Event(AUTH_SESSION_EXPIRED_EVENT));
  }
  return response;
}

export function getAuthHeader(): string | null {
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}
