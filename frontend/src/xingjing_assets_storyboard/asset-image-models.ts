import { useCallback, useEffect, useState } from "react";
import { AssetsStoryboardApiError } from "./api";
import type { GenerationService } from "@/xingjing_generation_center/services";
import type { AssetsStoryboardPort } from "./contracts";

export const assetImageCapabilities = {
  generateReferenceImage: "image_generation",
  generateTurnaround: "character_turnaround",
  generateExpressions: "character_expressions",
} as const;

export function useAssetImageModels(api: AssetsStoryboardPort, workspaceId: string, projectId?: string) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ key: string; value?: readonly GenerationService[]; error?: "modelDenied" | "modelError" }>();
  const key = JSON.stringify([workspaceId, projectId, revision]);
  const recheck = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!projectId || !api.getGenerationServices) return;
    const controller = new AbortController();
    void api.getGenerationServices({ workspaceId, projectId }, Object.values(assetImageCapabilities), controller.signal).then(
      (value) => { if (!controller.signal.aborted) setResult({ key, value }); },
      (error: unknown) => {
        if (!controller.signal.aborted) setResult({ key, error: error instanceof AssetsStoryboardApiError && error.status === 403 ? "modelDenied" : "modelError" });
      },
    );
    return () => controller.abort();
  }, [api, key, projectId, workspaceId]);
  const current = result?.key === key ? result : undefined;
  const offer = (capability: string) => current?.value?.find((service) => service.capability === capability);
  function supports(capability: string) {
    const service = offer(capability);
    return Boolean(service?.available && (capability === "image_generation" || service.acceptsReferences));
  }
  const notice = !api.getGenerationServices ? "modelError" : !current ? "checking" : current.error;
  return { supports, notice, offer, workspaceId, projectId, recheck };
}

export interface AssetImageControls {
  readonly models: ReturnType<typeof useAssetImageModels>;
  readonly activeAction?: string;
  readonly pending: boolean;
  readonly rejection?: string;
  readonly billingHref?: string;
  readonly canManageBilling: boolean;
}
