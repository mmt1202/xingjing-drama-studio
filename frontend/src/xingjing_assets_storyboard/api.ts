import type {
  ActionInput,
  AssetAction,
  AssetActionResult,
  AssetItem,
  AssetListQuery,
  AssetsStoryboardPort,
  PageResult,
  PromptTemplateItem,
  ShotAction,
  ShotActionResult,
  ShotItem,
  ShotListQuery,
  TaskReceipt,
  RightsEvidenceUpload,
  UploadReceipt,
  WorkspaceProjectScope,
} from "./contracts";

interface MarketItemPayload {
  readonly id: string;
  readonly source_workspace_id: string;
  readonly source_version_id: string;
  readonly title: string;
  readonly template_kind: string | null;
  readonly tags: readonly string[];
  readonly price_minor: number;
  readonly rights: {
    readonly id: string;
    readonly scope: string;
    readonly commercial_use: boolean;
    readonly inheritable_scopes: readonly string[];
    readonly allow_fork: boolean;
  };
  readonly revision: number;
  readonly published_at: string;
}

interface MarketPagePayload {
  readonly items: readonly MarketItemPayload[];
  readonly next_cursor: string | null;
}

interface ResponseMeta {
  readonly requestId?: string;
  readonly page?: { readonly nextToken?: string | null };
  readonly collectionVersion?: number;
}

interface Envelope<T> {
  readonly data: T;
  readonly meta?: ResponseMeta;
}

interface ErrorEnvelope {
  readonly error?: {
    readonly code?: string;
    readonly message?: string;
    readonly retryable?: boolean;
    readonly details?: unknown;
  };
  readonly meta?: ResponseMeta;
}

interface GenerationTaskDetailPayload {
  readonly task: {
    readonly task_id: string;
    readonly status: TaskReceipt["status"];
    readonly output_asset_ids: readonly string[];
    readonly failure?: { readonly message?: string } | null;
  };
  readonly generatedAssets: readonly { readonly asset_id: string }[];
  readonly progress?: { readonly percent?: number } | null;
  readonly billing?: {
    readonly currency?: string;
    readonly estimated_minor?: number;
    readonly actual_minor?: number;
    readonly status?: string;
  } | null;
}

interface IdentitySessionContext {
  readonly user?: {
    readonly id?: string;
    readonly displayName?: string;
    readonly email?: string;
  };
  readonly currentWorkspace?: {
    readonly id?: string;
    readonly name?: string;
    readonly role?: string;
    readonly status?: string;
  };
}

export class AssetsStoryboardApiError extends Error {
  constructor(
    public readonly status: number | null,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
    public readonly retryable = false,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "AssetsStoryboardApiError";
  }
}

export interface AssetsStoryboardApiOptions {
  readonly fetcher?: typeof fetch;
  readonly baseUrl?: string;
  readonly createId?: () => string;
  readonly getAccessToken?: () => string | null | Promise<string | null>;
}

function defaultId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function requireProject(scope: WorkspaceProjectScope): string {
  if (!scope.projectId?.trim())
    throw new Error("projectId is required for this operation");
  return encodeURIComponent(scope.projectId);
}

function actionProject(
  scope: WorkspaceProjectScope,
  input: ActionInput<AssetAction>,
): string {
  if (scope.projectId?.trim()) return scope.projectId;
  const ownerProjectId = input.payload?.ownerProjectId;
  if (typeof ownerProjectId !== "string" || !ownerProjectId.trim()) {
    throw new Error(
      "ownerProjectId is required when an asset action starts from the workspace library",
    );
  }
  return ownerProjectId;
}

function stableFingerprint(
  scope: WorkspaceProjectScope,
  path: string,
  body: unknown,
): string {
  return `${scope.workspaceId}:${scope.projectId ?? "workspace"}:${path}:${JSON.stringify(body)}`;
}

async function sha256(file: File): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    await file.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (part) =>
    part.toString(16).padStart(2, "0"),
  ).join("");
}

function marketAssetType(kind: string | null): AssetItem["type"] {
  if (kind === "character") return "character";
  if (kind === "storyboard") return "scene";
  if (kind === "style") return "costume";
  if (kind === "message") return "voice";
  return "prop";
}

function marketAsset(item: MarketItemPayload, referenceCount = 0): AssetItem {
  return {
    id: item.id,
    type: marketAssetType(item.template_kind),
    name: item.title,
    tags: item.tags,
    source: "marketplace",
    status: "frozen",
    rightsStatus: "verified",
    currentVersionId: item.source_version_id,
    version: item.revision,
    referenceCount,
    updatedAt: item.published_at,
    metadata: {
      marketItemId: item.id,
      sourceWorkspaceId: item.source_workspace_id,
      templateKind: item.template_kind,
      priceMinor: item.price_minor,
      commercialUse: item.rights.commercial_use,
      inheritableScopes: item.rights.inheritable_scopes,
      allowFork: item.rights.allow_fork,
      sourceVersionId: item.source_version_id,
      marketRevision: item.revision,
    },
    versions: [
      {
        id: item.source_version_id,
        versionNo: item.revision,
        status: "frozen",
        source: "marketplace",
        createdAt: item.published_at,
      },
    ],
    rights: {
      id: item.rights.id,
      licenseScope: item.rights.scope,
      status: "verified",
    },
  };
}

export function createAssetsStoryboardApi(
  options: AssetsStoryboardApiOptions = {},
): AssetsStoryboardPort {
  const fetcher = options.fetcher ?? fetch;
  const createId = options.createId ?? defaultId;
  const baseUrl = options.baseUrl ?? "/api/v1";
  const replayKeys = new Map<string, string>();

  async function request<T>(
    scope: WorkspaceProjectScope,
    path: string,
    init: RequestInit = {},
    signal?: AbortSignal,
  ): Promise<Envelope<T>> {
    const requestId = createId();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    headers.set("X-Request-Id", requestId);
    const token = await options.getAccessToken?.();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let response: Response;
    try {
      response = await fetcher(`${baseUrl}${path}`, {
        ...init,
        headers,
        signal,
        credentials: "include",
      });
    } catch (cause) {
      if (
        signal?.aborted ||
        (cause instanceof Error && cause.name === "AbortError")
      )
        throw cause;
      throw new AssetsStoryboardApiError(
        null,
        "NETWORK_ERROR",
        "网络请求失败",
        requestId,
        true,
        cause,
      );
    }
    const body = (await response.json().catch(() => ({}))) as Partial<
      Envelope<T>
    > &
      ErrorEnvelope;
    if (!response.ok) {
      throw new AssetsStoryboardApiError(
        response.status,
        body.error?.code ?? "REQUEST_FAILED",
        body.error?.message ?? "请求失败",
        body.meta?.requestId ??
          response.headers.get("X-Request-Id") ??
          requestId,
        body.error?.retryable ?? response.status >= 500,
        body.error?.details,
      );
    }
    return { data: body.data as T, meta: body.meta };
  }

  async function list<T>(
    scope: WorkspaceProjectScope,
    path: string,
    query: AssetListQuery | ShotListQuery,
    defaultSort: string,
    signal?: AbortSignal,
  ): Promise<PageResult<T>> {
    const search = new URLSearchParams({
      pageSize: String(query.pageSize ?? 20),
      sort: query.sort ?? defaultSort,
    });
    if (query.pageToken) search.set("pageToken", query.pageToken);
    if (query.search) search.set("search", query.search);
    if (query.status) search.set("status", query.status);
    if ("assetType" in query && query.assetType)
      search.set("assetType", query.assetType);
    if ("rightsStatus" in query && query.rightsStatus)
      search.set("rightsStatus", query.rightsStatus);
    if ("hasIssues" in query && query.hasIssues !== undefined)
      search.set("hasIssues", String(query.hasIssues));
    if (scope.episodeId) search.set("episodeId", scope.episodeId);
    const response = await request<readonly T[]>(
      scope,
      `${path}?${search}`,
      {},
      signal,
    );
    return {
      items: response.data,
      nextToken: response.meta?.page?.nextToken ?? null,
      collectionVersion: response.meta?.collectionVersion,
      requestId: response.meta?.requestId,
    };
  }

  async function action<T, TAction extends string>(
    scope: WorkspaceProjectScope,
    path: string,
    input: ActionInput<TAction>,
    signal?: AbortSignal,
  ): Promise<T> {
    const payload =
      (input.action === "commitImport" || input.action === "generateShots") &&
      scope.episodeId &&
      !input.payload?.episodeId
        ? { ...input.payload, episodeId: scope.episodeId }
        : (input.payload ?? {});
    const body = { action: input.action, targetId: input.targetId, payload };
    const fingerprint = stableFingerprint(scope, path, body);
    const idempotencyKey = replayKeys.get(fingerprint) ?? createId();
    replayKeys.set(fingerprint, idempotencyKey);
    const headers = new Headers({
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    });
    if (input.version !== undefined)
      headers.set("If-Match", `"${input.version}"`);
    const response = await request<T>(
      scope,
      path,
      { method: "POST", headers, body: JSON.stringify(body) },
      signal,
    );
    replayKeys.delete(fingerprint);
    return response.data;
  }

  return {
    getSessionContext: async (scope, signal) => {
      const raw = (
        await request<IdentitySessionContext>(
          scope,
          "/session/context",
          {},
          signal,
        )
      ).data;
      const workspace = raw.currentWorkspace;
      if (!raw.user?.id || !workspace?.id || workspace.status !== "ACTIVE") {
        throw new AssetsStoryboardApiError(
          403,
          "WORKSPACE_CONTEXT_REQUIRED",
          "当前没有可用工作区",
        );
      }
      const permissions =
        workspace.role === "OWNER" ||
        workspace.role === "ADMIN" ||
        workspace.role === "PLATFORM_ADMIN"
          ? ["asset.view", "asset.manage", "shot.view", "shot.manage"]
          : ["asset.view", "shot.view"];
      return {
        user: {
          id: raw.user.id,
          name: raw.user.displayName ?? raw.user.email ?? raw.user.id,
        },
        workspace: { id: workspace.id, name: workspace.name ?? workspace.id },
        permissions,
        featureFlags: {},
      };
    },
    listAssets: (scope, query = {}, signal) =>
      list<AssetItem>(
        scope,
        scope.projectId
          ? `/projects/${requireProject(scope)}/assets`
          : `/workspaces/${encodeURIComponent(scope.workspaceId)}/assets`,
        query,
        "updatedAt:desc,id:desc",
        signal,
      ),
    async listMarketAssets(scope, query = {}, signal) {
      if (query.marketItemId) {
        const itemPath = `/market/items/${encodeURIComponent(query.marketItemId)}`;
        const [detail, usage] = await Promise.all([
          request<MarketItemPayload>(scope, itemPath, {}, signal),
          request<{ reference_count: number }>(
            scope,
            `${itemPath}/usage`,
            {},
            signal,
          ),
        ]);
        return {
          items: [marketAsset(detail.data, usage.data.reference_count)],
          nextToken: null,
          requestId: detail.meta?.requestId,
        };
      }
      const search = new URLSearchParams({
        page_size: String(Math.min(query.pageSize ?? 20, 100)),
      });
      if (query.pageToken) search.set("cursor", query.pageToken);
      if (query.search) search.set("search", query.search);
      const kind =
        query.assetType === "character"
          ? "character"
          : query.assetType === "scene"
            ? "storyboard"
            : query.assetType === "costume"
              ? "style"
              : query.assetType === "voice"
                ? "message"
                : query.assetType === "prop"
                  ? "project"
                  : undefined;
      if (kind) search.append("kind", kind);
      const page = await request<MarketPagePayload>(
        scope,
        `/market/items?${search}`,
        {},
        signal,
      );
      const items = page.data.items
        .map(marketAsset)
        .filter(
          (item) =>
            !query.rightsStatus || item.rightsStatus === query.rightsStatus,
        );
      return {
        items,
        nextToken: page.data.next_cursor,
        requestId: page.meta?.requestId,
      };
    },
    listShots: (scope, query = {}, signal) =>
      list<ShotItem>(
        scope,
        `/projects/${requireProject(scope)}/shots`,
        query,
        "sequenceNo:asc,id:asc",
        signal,
      ),
    listPromptTemplates: async (scope, signal) =>
      (
        await request<readonly PromptTemplateItem[]>(
          scope,
          `/projects/${requireProject(scope)}/prompt-templates`,
          {},
          signal,
        )
      ).data,
    async createPromptTemplate(scope, input, signal) {
      const path = `/projects/${requireProject(scope)}/prompt-templates`;
      const fingerprint = stableFingerprint(scope, path, input);
      const idempotencyKey = replayKeys.get(fingerprint) ?? createId();
      replayKeys.set(fingerprint, idempotencyKey);
      const response = await request<PromptTemplateItem>(
        scope,
        path,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify(input),
        },
        signal,
      );
      replayKeys.delete(fingerprint);
      return response.data;
    },
    async updatePromptTemplate(scope, promptId, version, input, signal) {
      const path = `/projects/${requireProject(scope)}/prompt-templates/${encodeURIComponent(promptId)}`;
      const fingerprint = stableFingerprint(scope, path, { version, ...input });
      const idempotencyKey = replayKeys.get(fingerprint) ?? createId();
      replayKeys.set(fingerprint, idempotencyKey);
      const response = await request<PromptTemplateItem>(
        scope,
        path,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            "If-Match": `"${version}"`,
          },
          body: JSON.stringify(input),
        },
        signal,
      );
      replayKeys.delete(fingerprint);
      return response.data;
    },
    async archivePromptTemplate(scope, promptId, version, signal) {
      const path = `/projects/${requireProject(scope)}/prompt-templates/${encodeURIComponent(promptId)}`;
      const fingerprint = stableFingerprint(scope, path, {
        version,
        operation: "archive",
      });
      const idempotencyKey = replayKeys.get(fingerprint) ?? createId();
      replayKeys.set(fingerprint, idempotencyKey);
      await request<PromptTemplateItem>(
        scope,
        path,
        {
          method: "DELETE",
          headers: {
            "Idempotency-Key": idempotencyKey,
            "If-Match": `"${version}"`,
          },
        },
        signal,
      );
      replayKeys.delete(fingerprint);
    },
    async assetAction(scope, input, signal) {
      if (input.action === "licenseMarketAsset") {
        if (!input.targetId || input.version === undefined)
          throw new Error("market item and revision are required");
        const sourceVersionId = input.payload?.sourceVersionId;
        const projectName = input.payload?.projectName;
        const intendedCommercialUse = input.payload?.intendedCommercialUse;
        const requestedInheritableScopes =
          input.payload?.requestedInheritableScopes;
        if (
          typeof sourceVersionId !== "string" ||
          typeof projectName !== "string" ||
          typeof intendedCommercialUse !== "boolean" ||
          !Array.isArray(requestedInheritableScopes)
        ) {
          throw new Error("market fork input is incomplete");
        }
        const forkBody = {
          market_item_id: input.targetId,
          expected_market_revision: input.version,
          expected_source_version_id: sourceVersionId,
          project_name: projectName,
          intended_commercial_use: intendedCommercialUse,
          requested_inheritable_scopes: requestedInheritableScopes,
        };
        const fingerprint = stableFingerprint(scope, "/forks", forkBody);
        const idempotencyKey = replayKeys.get(fingerprint) ?? createId();
        replayKeys.set(fingerprint, idempotencyKey);
        const fork = await request<{ target_project_id: string }>(
          scope,
          "/forks",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": idempotencyKey,
            },
            body: JSON.stringify(forkBody),
          },
          signal,
        );
        replayKeys.delete(fingerprint);
        return { message: `已创建授权副本项目 ${fork.data.target_project_id}` };
      }
      return action<AssetActionResult, AssetAction>(
        scope,
        `/projects/${encodeURIComponent(actionProject(scope, input))}/assets/actions`,
        input,
        signal,
      );
    },
    async uploadRightsEvidence(scope, asset, file, signal) {
      const projectId = scope.projectId ?? asset.projectId;
      if (!projectId?.trim())
        throw new Error("asset owner project is required for rights evidence");
      const body = new FormData();
      body.set("file", file);
      const idempotencyKey = `rights-evidence:${asset.id}:${file.name}:${file.size}:${file.lastModified}`;
      const response = await request<RightsEvidenceUpload>(
        scope,
        `/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(asset.id)}/rights-evidence`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body,
        },
        signal,
      );
      return response.data;
    },
    async getGenerationTask(scope, task, signal) {
      const projectId = encodeURIComponent(
        scope.projectId?.trim() || task.projectId?.trim() || "",
      );
      if (!projectId)
        throw new Error("projectId is required to read a generation task");
      const detail = await request<GenerationTaskDetailPayload>(
        scope,
        `/generation-tasks/${encodeURIComponent(task.taskId)}?projectId=${projectId}`,
        {},
        signal,
      );
      const outputAssetIds = detail.data.generatedAssets.map(
        (asset) => asset.asset_id,
      );
      return {
        taskId: detail.data.task.task_id,
        projectId: decodeURIComponent(projectId),
        status: detail.data.task.status,
        progress: detail.data.progress?.percent,
        statusUrl: `/api/v1/generation-tasks/${encodeURIComponent(task.taskId)}?projectId=${projectId}`,
        outputAssetIds,
        outputUrls: outputAssetIds.map(
          (assetId) =>
            `${baseUrl}/generation-assets/${encodeURIComponent(assetId)}/content?projectId=${projectId}`,
        ),
        failureMessage: detail.data.task.failure?.message,
        billingCurrency: detail.data.billing?.currency,
        estimatedCostMinor: detail.data.billing?.estimated_minor,
        actualCostMinor: detail.data.billing?.actual_minor,
        billingStatus: detail.data.billing?.status,
        requestId: detail.meta?.requestId,
      };
    },
    shotAction: (scope, input, signal) =>
      action<ShotActionResult, ShotAction>(
        scope,
        `/projects/${requireProject(scope)}/shots/actions`,
        input,
        signal,
      ),
    uploadImportFile: async (scope, file, signal): Promise<UploadReceipt> => {
      const digest = await sha256(file);
      const created = await action<
        {
          uploadId: string;
          uploadUrl: string;
          uploadHeaders?: Record<string, string>;
        },
        string
      >(
        scope,
        "/uploads",
        {
          action: "createUpload",
          payload: {
            projectId: requireProject(scope),
            fileName: file.name,
            sizeBytes: file.size,
            mediaType: file.type || "application/octet-stream",
            sha256: digest,
          },
        },
        signal,
      );
      const uploadResponse = await fetcher(created.uploadUrl, {
        method: "PUT",
        body: file,
        headers: created.uploadHeaders,
        signal,
      });
      if (!uploadResponse.ok)
        throw new AssetsStoryboardApiError(
          uploadResponse.status,
          "UPLOAD_FAILED",
          "文件上传失败",
          undefined,
          uploadResponse.status >= 500,
        );
      await action(
        scope,
        `/projects/${requireProject(scope)}/uploads/${encodeURIComponent(created.uploadId)}/complete`,
        { action: "completeUpload", payload: { sha256: digest } },
        signal,
      );
      return {
        uploadId: created.uploadId,
        sha256: digest,
        fileName: file.name,
      };
    },
  };
}
