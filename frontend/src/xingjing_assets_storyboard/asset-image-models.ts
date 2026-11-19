import { useCallback, useEffect, useState } from "react";
import { AssetsStoryboardApiError } from "./api";
import type { AssetGenerationModels, AssetsStoryboardPort } from "./contracts";

export const assetImageCapabilities = {
  generateReferenceImage: "image_generation",
  generateTurnaround: "character_turnaround",
  generateExpressions: "character_expressions",
} as const;

export function useAssetImageModels(api: AssetsStoryboardPort, workspaceId: string, projectId?: string) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; value?: AssetGenerationModels; error?: "modelDenied" | "modelError" }>();
  const key = JSON.stringify([workspaceId, projectId, revision]);
  const recheck = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!projectId || !api.getGenerationModels) return;
    const controller = new AbortController();
    void api.getGenerationModels({ workspaceId, projectId }, controller.signal).then(
      (value) => { if (!controller.signal.aborted) setResult({ key, value }); },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ key, error: error instanceof AssetsStoryboardApiError && error.status === 403 ? "modelDenied" : "modelError" });
      },
    );
    return () => controller.abort();
  }, [api, key, projectId, workspaceId]);
  const current = result?.key === key ? result : undefined;
  const model = current?.value?.model;
  function supports(capability: string) {
    return Boolean(model?.submission_available && model.callback_available
      && (capability === "image_generation" || model.input_contract_available)
      && model.models?.some((item) => item.active && item.media_types.includes("image") && item.capabilities.includes(capability)));
  }
  const notice = !api.getGenerationModels ? "modelError" : !current ? "checking" : current.error;
  return { supports, notice, canManageModels: current?.value?.canManageModels === true, workspaceId, projectId, recheck };
}

export interface AssetImageControls {
  readonly models: ReturnType<typeof useAssetImageModels>;
  readonly activeAction?: string;
  readonly pending: boolean;
  readonly rejection?: string;
}
