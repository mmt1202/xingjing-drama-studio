import type { GenerationModelAvailability, GenerationTask } from "@/xingjing_generation_center/contracts";

export interface CoverScope { workspaceId: string; projectId: string; actorId: string }
export interface CoverPermissions { canView: boolean; canManage: boolean; canGenerate: boolean; canApply: boolean }
export interface CoverFinal { id: string; episodeId: string; revision: number; createdAt: string; durationMs: number }
export interface SafeArea { x: number; y: number; width: number; height: number }
export interface CoverPlan {
  id: string; name: string; finalVersionId: string; episodeId: string; target: string; prompt: string;
  aspectRatio: string; titleSafeArea: SafeArea; version: number; createdAt: string; updatedAt: string;
  selectedVariantId: string | null; recommendedVariantId: string | null; appliedSelectionId: string | null;
}
export interface CoverCandidate { assetId: string; taskId: string; createdAt: string; available: boolean; reasonCode?: string; contentSha256?: string }
export interface CoverVariant {
  id: string; label: string; coverAssetId: string; title: string; description: string; tags: string[];
  score: number | null; evaluationNote: string; evaluatedBy: string | null; evaluatedAt: string | null;
}
export interface CoverCatalog {
  scope: CoverScope; permissions: CoverPermissions; targets: string[]; aspectRatios: string[];
  finalVideos: CoverFinal[]; generationAvailability: GenerationModelAvailability | null;
}
export interface CoverDetail {
  scope: CoverScope; permissions: CoverPermissions; plan: CoverPlan; candidates: CoverCandidate[];
  variants: CoverVariant[]; generationTasks: (GenerationTask | { task_id: string; status: "unavailable"; resolved_model_id?: never; request?: never })[];
  history?: { id: string; version: number; action: string; actorId: string; createdAt: string }[];
}
export interface CoverPage { items: CoverPlan[]; total: number; offset: number; limit: number }
export interface PlanInput { name: string; finalVersionId: string; target: string; prompt: string; aspectRatio: string; titleSafeArea: SafeArea }
export interface VariantInput { id?: string; label: string; coverAssetId: string; title: string; description: string; tags: string[] }
export interface CoverAction { plan: CoverPlan; variant?: CoverVariant; task?: GenerationTask; selection?: { id: string } }
export interface CoverFilters { q: string; target: string; finalVersionId: string; episodeId: string; offset: number; limit: number }
export interface CoverApi {
  catalog(signal?: AbortSignal): Promise<CoverCatalog>;
  list(filters: CoverFilters, signal?: AbortSignal): Promise<CoverPage>;
  detail(id: string, signal?: AbortSignal): Promise<CoverDetail>;
  create(input: PlanInput): Promise<CoverAction>;
  action(plan: CoverPlan, action: string, payload?: object): Promise<CoverAction>;
  media(planId: string, assetId: string, signal?: AbortSignal): Promise<Blob>;
}
