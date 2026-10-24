import { createAuthenticatedFetcher } from "@/utils/auth";

export interface PreferenceRecord {
  resource: string;
  value: Record<string, unknown>;
  version: number;
  updatedAt: string;
}

export interface VersionHistoryRecord {
  kind: string;
  objectId: string;
  versionId: string;
  revision: number;
  summary: string;
  createdAt: string | null;
}

export class PreferenceApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public requestId?: string,
  ) {
    super(code);
  }
}

export interface PreferenceApi {
  get(resource: string, signal?: AbortSignal): Promise<PreferenceRecord | null>;
  put(
    resource: string,
    value: Record<string, unknown>,
    version: number,
    idempotencyKey: string,
  ): Promise<PreferenceRecord>;
  versions(projectId: string): Promise<VersionHistoryRecord[]>;
}

export function createPreferenceApi(options: {
  workspaceId: string;
  accessToken?: string | null;
  fetcher?: typeof fetch;
}): PreferenceApi {
  const fetcher = createAuthenticatedFetcher({ workspaceId: options.workspaceId, fetcher: options.fetcher });
  const headers = (): Record<string, string> => ({
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Workspace-Id": options.workspaceId,
    ...(options.accessToken
      ? { Authorization: `Bearer ${options.accessToken}` }
      : {}),
  });
  const parse = async <T>(response: Response): Promise<T> => {
    const body = (await response.json().catch(() => ({}))) as {
      error?: { code?: string };
      detail?: { code?: string };
      requestId?: string;
      meta?: { requestId?: string };
    };
    if (!response.ok) {
      throw new PreferenceApiError(
        response.status,
        body.error?.code ?? body.detail?.code ?? "PREFERENCE_REQUEST_FAILED",
        body.meta?.requestId ?? body.requestId,
      );
    }
    return body as T;
  };
  return {
    async get(resource, signal) {
      const result = await parse<{ items: PreferenceRecord[] }>(
        await fetcher(
          `/api/v1/account/preferences?resource=${encodeURIComponent(resource)}`,
          { credentials: "include", headers: headers(), signal },
        ),
      );
      if (!Array.isArray(result.items) || result.items.some((item) => !isPreferenceRecord(item) || item.resource !== resource)) {
        throw new PreferenceApiError(502, "PREFERENCE_RESPONSE_INVALID");
      }
      return result.items[0] ?? null;
    },
    async versions(projectId) {
      const result = await parse<{ items: VersionHistoryRecord[] }>(
        await fetcher(
          `/api/v1/account/version-history?projectId=${encodeURIComponent(projectId)}`,
          { credentials: "include", headers: headers() },
        ),
      );
      return result.items;
    },
    async put(resource, value, version, idempotencyKey) {
      const saved = await parse<PreferenceRecord>(
        await fetcher("/api/v1/account/preferences", {
          method: "PUT",
          credentials: "include",
          headers: { ...headers(), "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({ resource, value, version }),
        }),
      );
      if (!isPreferenceRecord(saved) || saved.resource !== resource) throw new PreferenceApiError(502, "PREFERENCE_RESPONSE_INVALID");
      return saved;
    },
  };
}

export function isPreferenceRecord(value: unknown): value is PreferenceRecord {
  if (!value || typeof value !== "object") return false;
  const record = value as Partial<PreferenceRecord>;
  return typeof record.resource === "string" && typeof record.value === "object" && record.value !== null
    && !Array.isArray(record.value) && Number.isSafeInteger(record.version) && (record.version ?? -1) >= 0
    && typeof record.updatedAt === "string";
}
