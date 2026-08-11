import type { ComponentProps, ComponentType } from "react";

import { M08EditingWorkspace } from "./M08EditingWorkspace";
import { PostproductionWorkspace } from "./PostproductionWorkspace";

export const postproductionRouteExports: ReadonlyArray<{ path: string; component: ComponentType<ComponentProps<typeof PostproductionWorkspace>> }> = [
  { path: "/workspaces/:workspaceId/projects/:projectId/postproduction", component: PostproductionWorkspace },
  { path: "/workspaces/:workspaceId/projects/:projectId/compliance-delivery", component: PostproductionWorkspace },
];

export const m08RouteExports: ReadonlyArray<{
  path: string;
  pageId: string;
  component: ComponentType<ComponentProps<typeof M08EditingWorkspace>>;
}> = [
  { path: "/creator/editing-suite.html", pageId: "CR-028", component: M08EditingWorkspace },
  { path: "/creator/final-render.html", pageId: "CR-036", component: M08EditingWorkspace },
  { path: "/creator/final-version-compare.html", pageId: "CR-037", component: M08EditingWorkspace },
  { path: "/creator/timeline-preview.html", pageId: "CR-114", component: M08EditingWorkspace },
  { path: "/creator/watermark-editor.html", pageId: "CR-122", component: M08EditingWorkspace },
];
