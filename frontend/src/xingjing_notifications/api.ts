import i18n from "@/i18n";
import { createAuthenticatedFetcher } from "@/utils/auth";
import type { NotificationPage, NotificationPreference, NotificationsApi, NotificationScope, NotificationTarget } from "./contracts";

export class NotificationApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly requestId?: string) { super(code); }
}

export function createNotificationsApi(scope: NotificationScope, token: string): NotificationsApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId: scope.workspaceId, actorId: scope.actorId });
  async function request<T>(path: string, signal: AbortSignal, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("Accept-Language", i18n.resolvedLanguage ?? "zh");
    headers.set("Authorization", `Bearer ${token}`);
    headers.set("X-Workspace-Id", scope.workspaceId);
    headers.set("X-Expected-Actor-Id", scope.actorId);
    headers.set("X-Request-Id", crypto.randomUUID());
    const response = await fetcher(`/api/v1${path}`, { ...init, signal, headers, credentials: "same-origin", cache: "no-store" });
    const body = await response.json().catch(() => null) as {
      data?: T; error?: { code?: string }; detail?: { code?: string };
      meta?: { requestId?: string; scope?: NotificationScope };
    } | null;
    if (!response.ok || !body || body.data === undefined) {
      throw new NotificationApiError(response.status, body?.error?.code ?? body?.detail?.code ?? "NOTIFICATIONS_UNAVAILABLE", body?.meta?.requestId);
    }
    if (body.meta?.scope?.workspaceId !== scope.workspaceId || body.meta.scope.actorId !== scope.actorId) {
      throw new NotificationApiError(409, "WORKSPACE_CONTEXT_MISMATCH", body.meta?.requestId);
    }
    return body.data;
  }
  return {
    list: (query, signal) => request<NotificationPage>(`/notifications?${new URLSearchParams({ unreadOnly: String(query.unreadOnly), offset: String(query.offset), limit: String(query.limit) })}`, signal),
    read: (notificationId, signal) => request<{ updated: number }>("/notifications/actions", signal, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(notificationId ? { action: "read", notificationId } : { action: "read_all" }),
    }),
    preferences: (signal) => request<NotificationPreference[]>("/notification-preferences", signal),
    savePreference: (channel, input, signal) => request<NotificationPreference>(`/notification-preferences/${channel}`, signal, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    }),
    target: (notificationId, signal) => request<NotificationTarget>(`/notifications/${encodeURIComponent(notificationId)}/target`, signal),
  };
}
