export interface NotificationScope { workspaceId: string; actorId: string }
export interface InboxNotification {
  id: string;
  category: string;
  title: string;
  body: string;
  resourceType: string | null;
  resourceId: string | null;
  readAt: string | null;
  createdAt: string;
}
export interface NotificationPage {
  items: InboxNotification[];
  unreadCount: number;
  total: number;
  offset: number;
  limit: number;
}
export type NotificationChannel = "email" | "sms" | "push";
export interface NotificationPreference {
  channel: NotificationChannel;
  enabled: boolean;
  destination: string | null;
  version: string;
  updatedAt: string;
}
export interface PlatformTaskSummary {
  id: string;
  taskType: string;
  status: string;
  progress: number;
  attemptCount: number;
  maxAttempts: number;
  version: number;
  createdAt: string;
  updatedAt: string;
}
export type NotificationTarget =
  | { kind: "project"; projectId: string }
  | { kind: "platform-task"; projectId: string | null; task: PlatformTaskSummary; errorCode: string | null };
export interface NotificationsApi {
  list(query: { unreadOnly: boolean; offset: number; limit: number }, signal: AbortSignal): Promise<NotificationPage>;
  read(notificationId: string | null, signal: AbortSignal): Promise<{ updated: number }>;
  preferences(signal: AbortSignal): Promise<NotificationPreference[]>;
  savePreference(channel: NotificationChannel, input: { enabled: boolean; destination: string | null; expectedVersion: string | null }, signal: AbortSignal): Promise<NotificationPreference>;
  target(notificationId: string, signal: AbortSignal): Promise<NotificationTarget>;
}
