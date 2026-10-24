import { dashboardDefaults } from "@/xingjing_analytics/query";
import { homePreferences } from "@/xingjing_creator_home/HomePreferences";
import { isPreferenceRecord, type PreferenceRecord } from "./api";

export type SettingsResource = "creator-home" | "data-dashboard";
export interface SettingsScope { actorId: string; workspaceId: string }
export interface SettingsDraft extends SettingsScope {
  resource: SettingsResource;
  base: PreferenceRecord | null;
  values: Record<string, unknown>;
  intent?: { key: string; value: Record<string, unknown>; version: number };
}

const homeSchema = {
  showRecent: [true, false], showTasks: [true, false], showInspiration: [true, false],
  defaultMode: ["pilot", "series", "comic", "realistic"], recentDays: [0, 7, 30], recentLimit: [3, 6, 9],
};
const dashboardSchema = { range: ["week", "month", "quarter", "all"], groupBy: ["project", "episode", "model", "member"] };

export function settingsValues(resource: SettingsResource, value?: Record<string, unknown>): Record<string, unknown> {
  return resource === "creator-home" ? { ...homePreferences(value) } : { ...dashboardDefaults(value) };
}

/** Validate exact scalar types; retain opaque fields instead of normalizing them away. */
export function settingsValueIssue(resource: SettingsResource, value: Record<string, unknown>): "invalid" | "unknown" | null {
  const schema: Record<string, readonly unknown[]> = resource === "creator-home" ? homeSchema : dashboardSchema;
  if (Object.entries(schema).some(([field, allowed]) => Object.hasOwn(value, field) && !allowed.includes(value[field]))) return "invalid";
  if (resource === "data-dashboard" && Object.keys(value).some((field) => !Object.hasOwn(schema, field))) return "unknown";
  return null;
}

export function settingsDraftKey(scope: SettingsScope, resource: SettingsResource): string {
  return `xingjing.settings-draft.v1:${encodeURIComponent(scope.actorId)}:${encodeURIComponent(scope.workspaceId)}:${resource}`;
}

export function readSettingsDraft(scope: SettingsScope, resource: SettingsResource): SettingsDraft | null {
  try {
    const raw = sessionStorage.getItem(settingsDraftKey(scope, resource));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return null;
    const draft = value as SettingsDraft;
    if (draft.actorId !== scope.actorId || draft.workspaceId !== scope.workspaceId || draft.resource !== resource
      || draft.base !== null && (!isPreferenceRecord(draft.base) || draft.base.resource !== resource)
      || !draft.values || typeof draft.values !== "object" || Array.isArray(draft.values)
      || settingsValueIssue(resource, draft.values)) return null;
    const complete = settingsValues(resource, draft.values);
    if (Object.keys(complete).some((field) => !Object.hasOwn(draft.values, field))) return null;
    if (draft.intent && (typeof draft.intent.key !== "string" || !/^[A-Za-z0-9._:/-]{1,128}$/.test(draft.intent.key)
      || !Number.isSafeInteger(draft.intent.version) || draft.intent.version !== (draft.base?.version ?? 0)
      || !draft.intent.value || typeof draft.intent.value !== "object" || Array.isArray(draft.intent.value)
      || JSON.stringify(draft.intent.value) !== JSON.stringify({ ...draft.base?.value, ...draft.values }))) return null;
    return draft;
  } catch { return null; }
}

export function persistSettingsDraft(scope: SettingsScope, resource: SettingsResource, draft: SettingsDraft | null): boolean {
  try {
    const key = settingsDraftKey(scope, resource);
    if (draft) sessionStorage.setItem(key, JSON.stringify(draft));
    else sessionStorage.removeItem(key);
    return true;
  } catch { return false; }
}
