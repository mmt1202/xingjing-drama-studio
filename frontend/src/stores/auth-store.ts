import { create } from "zustand";
import { AUTH_SESSION_EXPIRED_EVENT, AUTH_TOKEN_REFRESHED_EVENT, authenticatedFetch, getToken, setToken as saveToken, setRefreshToken, clearToken, refreshAccessToken } from "@/utils/auth";

interface AuthState {
  token: string | null;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  initialize: () => void;
  login: (token: string, username: string, refreshToken?: string, accessExpiresAt?: string) => void;
  logout: () => Promise<void>;
  setLoading: (loading: boolean) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  username: null,
  isAuthenticated: false,
  isLoading: true,

  initialize: () => {
    const token = getToken();
    if (token) {
      fetch("/api/v1/account/profile", { headers: { Authorization: `Bearer ${token}` } })
        .then(async (response) => {
          if (response.ok) return token;
          if (response.status === 401) return refreshAccessToken();
          return null;
        })
        .then((verifiedToken) => {
          if (verifiedToken) set({ token: verifiedToken, isAuthenticated: true, isLoading: false });
          else { clearToken(); set({ token: null, isAuthenticated: false, isLoading: false }); }
        })
        .catch(() => set({ isLoading: false }));
      return;
    }
    // 无 token 时先问后端是否启用了鉴权。`AUTH_ENABLED=false` 时后端全链路
    // bypass，前端也应该跳过登录页直接进主界面。超时 / 网络异常 / 响应 shape
    // 异常时 fail-closed 退回到登录页，避免误把损坏响应当成"无需鉴权"放行。
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    fetch("/api/v1/auth/status", { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`status ${res.status}`);
        const payload: unknown = await res.json();
        if (
          typeof payload !== "object" ||
          payload === null ||
          typeof (payload as { enabled?: unknown }).enabled !== "boolean"
        ) {
          throw new Error("invalid /auth/status payload");
        }
        const { enabled } = payload as { enabled: boolean };
        if (!enabled) {
          set({ isAuthenticated: true });
        }
      })
      .catch((err) => {
        console.warn("[auth] /auth/status fetch failed; defaulting to login", err);
      })
      .finally(() => {
        clearTimeout(timeoutId);
        set({ isLoading: false });
      });
  },

  login: (token, username, refreshToken, accessExpiresAt) => {
    saveToken(token, accessExpiresAt);
    if (refreshToken) setRefreshToken(refreshToken);
    set({ token, username, isAuthenticated: true, isLoading: false });
  },

  logout: async () => {
    const token = getToken();
    if (token) {
      await authenticatedFetch("/api/v1/auth/logout", {
        method: "POST",
      }).catch(() => undefined);
    }
    clearToken();
    set({ token: null, username: null, isAuthenticated: false });
  },

  setLoading: (isLoading) => set({ isLoading }),
}));

globalThis.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, (event) => {
  const detail: unknown = event instanceof CustomEvent ? event.detail : null;
  const accessToken = typeof detail === "object" && detail !== null
    && "accessToken" in detail && typeof detail.accessToken === "string"
    ? detail.accessToken
    : getToken();
  if (accessToken) useAuthStore.setState({ token: accessToken, isAuthenticated: true, isLoading: false });
});

globalThis.addEventListener(AUTH_SESSION_EXPIRED_EVENT, () => {
  useAuthStore.setState({ token: null, username: null, isAuthenticated: false, isLoading: false });
});
