export interface GenerationService {
  readonly capability: string;
  readonly available: boolean;
  readonly canViewBilling: boolean;
  readonly canManageBilling: boolean;
  readonly acceptsReferences: boolean;
  readonly estimatedMinor?: number;
  readonly successfulOutputMinor?: number | null;
  readonly currency?: string;
}

export function generationServicesPath(projectId: string, mediaType: "image" | "video", capabilities: readonly string[]) {
  const query = new URLSearchParams({ projectId, mediaType });
  for (const capability of capabilities) query.append("capability", capability);
  return `/generation-services?${query}`;
}
