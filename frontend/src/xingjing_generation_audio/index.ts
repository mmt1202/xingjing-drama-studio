export {
  GenerationAudioWorkspace,
  type AudioPageId,
  type GenerationAudioWorkspaceProps,
} from "./GenerationAudioWorkspace";
export { createGenerationAudioApi, generationAudioApi, XingjingApiError } from "./api";
export * from "./contracts";
export const xingjingGenerationAudioRoutes = [
  { path: "/workspaces/:workspaceId/projects/:projectId/generation", tab: "generation" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/audio", pageId: "CR-007" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/audio-subtitle", pageId: "CR-007" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/bgm-sfx-mixer", pageId: "CR-008" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/lip-sync-check", pageId: "CR-056" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/lip-sync-failed-fallback", pageId: "CR-057" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/lip-sync-version-compare", pageId: "CR-058" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/lip-sync", pageId: "CR-059" as const },
  { path: "/workspaces/:workspaceId/projects/:projectId/subtitle-timeline", pageId: "CR-105" as const },
];
