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
  get(resource: string): Promise<PreferenceRecord | null>;
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
  const fetcher = options.fetcher ?? fetch;
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
    };
    if (!response.ok) {
      throw new PreferenceApiError(
        response.status,
        body.error?.code ?? body.detail?.code ?? "PREFERENCE_REQUEST_FAILED",
        body.requestId,
      );
    }
    return body as T;
  };
  return {
    async get(resource) {
      const result = await parse<{ items: PreferenceRecord[] }>(
        await fetcher(
          `/api/v1/account/preferences?resource=${encodeURIComponent(resource)}`,
          { credentials: "include", headers: headers() },
        ),
      );
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
      return parse<PreferenceRecord>(
        await fetcher("/api/v1/account/preferences", {
          method: "PUT",
          credentials: "include",
          headers: { ...headers(), "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({ resource, value, version }),
        }),
      );
    },
  };
}
