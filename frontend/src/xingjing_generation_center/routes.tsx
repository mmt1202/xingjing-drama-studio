import type { ComponentType } from "react";

import { GenerationWorkbench, type GenerationWorkbenchProps } from "./GenerationWorkbench";

export type GenerationCenterRouteProps = GenerationWorkbenchProps;
export const GenerationCenterPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="center" />;
export const ImageGenerationPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="image" />;
export const ImageToVideoPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="image-to-video" />;
export const TextToVideoPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="text-to-video" />;
export const VideoGenerationPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video" />;
export const TaskCenterPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="tasks" />;
export const FirstLastVideoPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="first-last-video" />;
export const ImageEditPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="image-edit" />;
export const InpaintPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="inpaint" />;
export const ReferenceFusionPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="reference-fusion" />;
export const ImageUpscalePage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="image-upscale" />;
export const ModelCenterPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="model-center" />;
export const ModelDetailPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="model-detail" />;
export const ModelMarketPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="model-market" />;
export const ModelReportPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="model-report" />;
export const VideoCandidateComparisonPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video-candidates" />;
export const VideoExtendPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video-extend" />;
export const VideoRetryPage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video-retry" />;
export const VideoStabilizePage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video-stabilize" />;
export const VideoUpscalePage = (props: GenerationCenterRouteProps) => <GenerationWorkbench {...props} view="video-upscale" />;

export interface GenerationCenterRouteExport { readonly pageId: string; readonly path: string; readonly component: ComponentType<GenerationCenterRouteProps> }
export const generationCenterRouteExports: readonly GenerationCenterRouteExport[] = [
  { pageId: "CR-044", path: "/workspaces/:workspaceId/projects/:projectId/generation", component: GenerationCenterPage },
  { pageId: "CR-049", path: "/workspaces/:workspaceId/projects/:projectId/generation/image", component: ImageGenerationPage },
  { pageId: "CR-052", path: "/workspaces/:workspaceId/projects/:projectId/generation/image-to-video", component: ImageToVideoPage },
  { pageId: "CR-113", path: "/workspaces/:workspaceId/projects/:projectId/generation/text-to-video", component: TextToVideoPage },
  { pageId: "CR-118", path: "/workspaces/:workspaceId/projects/:projectId/generation/video", component: VideoGenerationPage },
  { pageId: "CR-106", path: "/workspaces/:workspaceId/projects/:projectId/tasks", component: TaskCenterPage },
  { pageId: "CR-038", path: "/workspaces/:workspaceId/projects/:projectId/generation/first-last-video", component: FirstLastVideoPage },
  { pageId: "CR-048", path: "/workspaces/:workspaceId/projects/:projectId/generation/image-edit", component: ImageEditPage },
  { pageId: "CR-050", path: "/workspaces/:workspaceId/projects/:projectId/generation/inpaint", component: InpaintPage },
  { pageId: "CR-051", path: "/workspaces/:workspaceId/projects/:projectId/generation/reference-fusion", component: ReferenceFusionPage },
  { pageId: "CR-053", path: "/workspaces/:workspaceId/projects/:projectId/generation/image-upscale", component: ImageUpscalePage },
  { pageId: "CR-063", path: "/workspaces/:workspaceId/projects/:projectId/generation/model-center", component: ModelCenterPage },
  { pageId: "CR-064", path: "/workspaces/:workspaceId/projects/:projectId/generation/model-detail", component: ModelDetailPage },
  { pageId: "CR-065", path: "/workspaces/:workspaceId/projects/:projectId/generation/model-market", component: ModelMarketPage },
  { pageId: "CR-066", path: "/workspaces/:workspaceId/projects/:projectId/generation/model-report", component: ModelReportPage },
  { pageId: "CR-116", path: "/workspaces/:workspaceId/projects/:projectId/generation/video-candidates", component: VideoCandidateComparisonPage },
  { pageId: "CR-117", path: "/workspaces/:workspaceId/projects/:projectId/generation/video-extend", component: VideoExtendPage },
  { pageId: "CR-119", path: "/workspaces/:workspaceId/projects/:projectId/generation/video-retry", component: VideoRetryPage },
  { pageId: "CR-120", path: "/workspaces/:workspaceId/projects/:projectId/generation/video-stabilize", component: VideoStabilizePage },
  { pageId: "CR-121", path: "/workspaces/:workspaceId/projects/:projectId/generation/video-upscale", component: VideoUpscalePage },
];
