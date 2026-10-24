import { create } from "zustand";
import { AUTH_SESSION_CHANGED_EVENT, AUTH_SESSION_EXPIRED_EVENT, AUTH_TOKEN_REFRESHED_EVENT, authenticatedFetch, clearToken, getSessionEpoch, getToken, isAuthStorageKey, startAuthSession } from "@/utils/auth";

interface AuthState {
  token: string | null;
  sessionEpoch: string;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  initialize: () => void;
  login: (token: string, username: string, refreshToken?: string, accessExpiresAt?: string) => void;
  logout: () => Promise<void>;
  setLoading: (loading: boolean) => void;
}
export const useAuthStore = create<AuthState>((set) => ({
  token: null, sessionEpoch: getSessionEpoch(), username: null, isAuthenticated: false, isLoading: true,
  initialize: () => {
    const epoch = getSessionEpoch();
    if (getToken()) {
      const settle = () => {
        if (getSessionEpoch() !== epoch) return;
        // A transport outage does not reject the saved session; business context still requires server verification.
        const token = getToken();
        set({ token, sessionEpoch: epoch, isAuthenticated: Boolean(token), isLoading: false });
      };
      void authenticatedFetch("/api/v1/account/profile").then(settle).catch(settle);
      return;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    void fetch("/api/v1/auth/status", { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("AUTH_STATUS_UNAVAILABLE");
      const value: unknown = await response.json();
      if (!value || typeof value !== "object" || !("enabled" in value) || typeof value.enabled !== "boolean") throw new Error("AUTH_STATUS_INVALID");
      if (getSessionEpoch() === epoch && !getToken() && !value.enabled) set({ sessionEpoch: epoch, isAuthenticated: true });
    }).catch(() => undefined).finally(() => { clearTimeout(timeoutId); if (getSessionEpoch() === epoch) set({ isLoading: false }); });
  },
  login: (token, username, refreshToken, accessExpiresAt) => {
    const sessionEpoch = startAuthSession(token, refreshToken, accessExpiresAt);
    set({ token, sessionEpoch, username, isAuthenticated: true, isLoading: false });
  },
  logout: async () => {
    const token = getToken(), epoch = getSessionEpoch();
    clearToken(epoch);
    if (token) await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin", headers: { Authorization: "Bearer " + token } }).catch(() => undefined);
  },
  setLoading: (isLoading) => set({ isLoading }),
}));
function synchronizeSession() {
  const sessionEpoch = getSessionEpoch(), token = getToken();
  const current = useAuthStore.getState();
  useAuthStore.setState({ token, sessionEpoch, ...(current.sessionEpoch !== sessionEpoch ? { username: null } : {}), isAuthenticated: Boolean(token), isLoading: false });
}
for (const eventName of [AUTH_TOKEN_REFRESHED_EVENT, AUTH_SESSION_CHANGED_EVENT, AUTH_SESSION_EXPIRED_EVENT]) {
  globalThis.addEventListener(eventName, (event) => {
    const detail: unknown = event instanceof CustomEvent ? event.detail : null;
    if (detail && typeof detail === "object" && "sessionEpoch" in detail && detail.sessionEpoch === getSessionEpoch()) synchronizeSession();
  });
}
globalThis.addEventListener("storage", (event) => { if (event instanceof StorageEvent && isAuthStorageKey(event.key)) synchronizeSession(); });
