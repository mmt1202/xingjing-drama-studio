export interface ApiMeta { requestId: string; serverTime: string }
export interface ApiEnvelope<T> { data: T; meta: ApiMeta }
export interface ApiErrorPayload { code: string; message: string; details: unknown[]; retryable: boolean }

export interface UserProfile { id: string; email: string; displayName: string }
export interface WorkspaceSummary {
  id: string; name: string; slug?: string; status?: string; kind?: string; planCode?: string;
  role: string; lastSelectedAt?: string | null; version?: number;
}
export interface RegistrationResult { user: UserProfile; workspace: WorkspaceSummary }
export interface SessionTokens {
  accessToken: string; refreshToken: string; accessExpiresAt: string; refreshExpiresAt: string;
}
export interface SessionContext {
  user: UserProfile; currentWorkspace: WorkspaceSummary | null; workspaces: WorkspaceSummary[];
}
export interface RegisterInput { email: string; password: string; displayName: string }
export interface LoginInput { email: string; password: string; deviceName?: string }

export interface TokenStore {
  get(): SessionTokens | null;
  set(tokens: SessionTokens): void;
  clear(): void;
}

export interface IdentityClient {
  register(input: RegisterInput): Promise<RegistrationResult>;
  login(input: LoginInput): Promise<SessionTokens>;
  getProfile(): Promise<UserProfile>;
  getSessionContext(): Promise<SessionContext>;
  selectWorkspace(workspaceId: string): Promise<SessionContext>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId?: string;

  constructor(input: { status: number; code: string; message: string; retryable: boolean; requestId?: string }) {
    super(input.message);
    this.name = "ApiError";
    this.status = input.status;
    this.code = input.code;
    this.retryable = input.retryable;
    this.requestId = input.requestId;
  }
}

export function createMemoryTokenStore(initial: SessionTokens | null = null): TokenStore {
  let value = initial;
  return { get: () => value, set: (tokens) => { value = tokens; }, clear: () => { value = null; } };
}

function validSessionContext(value: SessionContext): boolean {
  return Boolean(value && typeof value.user?.id === "string" && value.user.id.trim()
    && Array.isArray(value.workspaces) && value.workspaces.every((workspace) => typeof workspace?.id === "string" && workspace.id.trim())
    && (value.currentWorkspace === null || typeof value.currentWorkspace?.id === "string"
      && value.workspaces.some((workspace) => workspace.id === value.currentWorkspace?.id)));
}

/** A selected workspace is accepted only after a fresh identity read confirms it. */
export async function resolveLoginContext(client: IdentityClient): Promise<SessionContext> {
  const initial = await client.getSessionContext();
  if (!validSessionContext(initial)) throw new ApiError({ status: 502, code: "SESSION_CONTEXT_INVALID", message: "SESSION_CONTEXT_INVALID", retryable: true });
  if (initial.currentWorkspace || initial.workspaces.length !== 1) return initial;
  const requestedId = initial.workspaces[0].id;
  await client.selectWorkspace(requestedId);
  const confirmed = await client.getSessionContext();
  if (!validSessionContext(confirmed) || confirmed.user.id !== initial.user.id || confirmed.currentWorkspace?.id !== requestedId) {
    throw new ApiError({ status: 409, code: "WORKSPACE_SELECTION_NOT_CONFIRMED", message: "WORKSPACE_SELECTION_NOT_CONFIRMED", retryable: true });
  }
  return confirmed;
}

interface ClientOptions {
  fetcher?: typeof fetch;
  tokenStore: TokenStore;
  baseUrl?: string;
  createIdempotencyKey?: () => string;
}

export function createIdentityClient({
  fetcher = fetch,
  tokenStore,
  baseUrl = "",
  createIdempotencyKey = () => crypto.randomUUID(),
}: ClientOptions): IdentityClient {
  async function request<T>(path: string, init: RequestInit = {}, authenticated = false): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (authenticated) {
      const tokens = tokenStore.get();
      if (tokens) headers.set("Authorization", `Bearer ${tokens.accessToken}`);
    }

    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, { ...init, headers: Object.fromEntries(headers.entries()) });
    } catch {
      throw new ApiError({ status: 0, code: "NETWORK_ERROR", message: "网络连接失败，请检查连接后重试", retryable: true });
    }

    let body: unknown;
    try { body = await response.json(); } catch { body = null; }
    const envelope = body as Partial<ApiEnvelope<T>> & { error?: Partial<ApiErrorPayload>; meta?: Partial<ApiMeta> };
    if (!response.ok) {
      if (response.status === 401) tokenStore.clear();
      throw new ApiError({
        status: response.status,
        code: envelope?.error?.code ?? "HTTP_ERROR",
        message: envelope?.error?.message ?? "服务响应异常，请稍后重试",
        retryable: envelope?.error?.retryable ?? response.status >= 500,
        requestId: envelope?.meta?.requestId,
      });
    }
    if (!envelope || !("data" in envelope)) {
      throw new ApiError({ status: response.status, code: "INVALID_RESPONSE", message: "服务响应格式无效", retryable: false });
    }
    return envelope.data as T;
  }

  return {
    register: (input) => request("/api/v1/auth/register", { method: "POST", body: JSON.stringify(input) }),
    async login(input) {
      const result = await request<SessionTokens>("/api/v1/auth/login", { method: "POST", body: JSON.stringify(input) });
      tokenStore.set(result);
      return result;
    },
    getProfile: () => request("/api/v1/account/profile", {}, true),
    getSessionContext: () => request("/api/v1/session/context", {}, true),
    selectWorkspace: (workspaceId) => request("/api/v1/session/context/workspace", {
      method: "PUT",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify({ workspaceId }),
    }, true),
  };
}
