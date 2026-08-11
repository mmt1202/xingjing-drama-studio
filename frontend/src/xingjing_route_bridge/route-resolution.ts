import { adminRoutes } from "@/xingjing_admin_platform/routes";

export type ProjectSurface =
  | { kind: "project-library" }
  | { kind: "project-create"; mode: "quick" | "guided" }
  | { kind: "project-control" }
  | { kind: "script-import" }
  | { kind: "ai-director" }
  | { kind: "director-assets" }
  | { kind: "script-checkup" }
  | { kind: "script-lab" }
  | { kind: "source" }
  | { kind: "source-mapping" }
  | { kind: "rewrite" }
  | { kind: "script-versions" }
  | { kind: "generation"; initialTab: "generation" }
  | { kind: "audio"; pageId: string }
  | { kind: "postproduction"; pageId: string }
  | { kind: "compliance-delivery"; pageId: string }
  | { kind: "creator-settings"; pageId: string }
  | { kind: "assets-storyboard"; pageId: string };

const PROJECT_SURFACES: Readonly<Record<string, ProjectSurface>> = {
  projects: { kind: "project-library" },
  create: { kind: "project-create", mode: "quick" },
  "guided-create": { kind: "project-create", mode: "guided" },
  control: { kind: "project-control" },
  "script-import": { kind: "script-import" },
  "ai-director": { kind: "ai-director" },
  "director-assets": { kind: "director-assets" },
  "script-checkup": { kind: "script-checkup" },
  "script-lab": { kind: "script-lab" },
  source: { kind: "source" },
  "source-mapping": { kind: "source-mapping" },
  rewrite: { kind: "rewrite" },
  versions: { kind: "script-versions" },
  generation: { kind: "generation", initialTab: "generation" },
  audio: { kind: "audio", pageId: "CR-007" },
  "audio-subtitle": { kind: "audio", pageId: "CR-007" },
  "bgm-sfx-mixer": { kind: "audio", pageId: "CR-008" },
  "lip-sync-check": { kind: "audio", pageId: "CR-056" },
  "lip-sync-failed-fallback": { kind: "audio", pageId: "CR-057" },
  "lip-sync-version-compare": { kind: "audio", pageId: "CR-058" },
  "lip-sync": { kind: "audio", pageId: "CR-059" },
  "subtitle-timeline": { kind: "audio", pageId: "CR-105" },
  postproduction: { kind: "postproduction", pageId: "CR-028" },
  "editing-suite": { kind: "postproduction", pageId: "CR-028" },
  "final-render": { kind: "postproduction", pageId: "CR-036" },
  "final-version-compare": { kind: "postproduction", pageId: "CR-037" },
  "timeline-preview": { kind: "postproduction", pageId: "CR-114" },
  "watermark-editor": { kind: "postproduction", pageId: "CR-122" },
  "compliance-delivery": { kind: "compliance-delivery", pageId: "CR-018" },
  compliance: { kind: "compliance-delivery", pageId: "CR-018" },
  exports: { kind: "compliance-delivery", pageId: "CR-031" },
  "export-configuration": { kind: "compliance-delivery", pageId: "CR-032" },
  "export-history": { kind: "compliance-delivery", pageId: "CR-033" },
  provenance: { kind: "compliance-delivery", pageId: "CR-062" },
  "aigc-labeling": { kind: "compliance-delivery", pageId: "CR-004" },
  "compliance-report": { kind: "compliance-delivery", pageId: "CR-019" },
  rights: { kind: "compliance-delivery", pageId: "CR-020" },
  "cost-report": { kind: "compliance-delivery", pageId: "CR-021" },
  "davinci-edl": { kind: "compliance-delivery", pageId: "CR-026" },
  "likeness-risk": { kind: "compliance-delivery", pageId: "CR-047" },
  "ip-rights": { kind: "compliance-delivery", pageId: "CR-054" },
  "jianying-export": { kind: "compliance-delivery", pageId: "CR-055" },
  "sensitive-words": { kind: "compliance-delivery", pageId: "CR-071" },
  "premiere-xml": { kind: "compliance-delivery", pageId: "CR-072" },
  "release-materials": { kind: "compliance-delivery", pageId: "CR-080" },
  "release-package-detail": { kind: "compliance-delivery", pageId: "CR-081" },
  "release-rules": { kind: "compliance-delivery", pageId: "CR-082" },
  "release-package": { kind: "compliance-delivery", pageId: "CR-083" },
  authorizations: { kind: "compliance-delivery", pageId: "CR-086" },
  "cover-generate": { kind: "creator-settings", pageId: "CR-022" },
  "cover-title-ab-test": { kind: "creator-settings", pageId: "CR-023" },
  home: { kind: "creator-settings", pageId: "CR-046" },
  "risk-appeal": { kind: "compliance-delivery", pageId: "CR-087" },
  settings: { kind: "creator-settings", pageId: "CR-096" },
  "version-history": { kind: "creator-settings", pageId: "CR-115" },
  "assets-extract": { kind: "assets-storyboard", pageId: "CR-027" },
  assets: { kind: "assets-storyboard", pageId: "M04-ASSET-LIST" },
  "asset-editor": { kind: "assets-storyboard", pageId: "M04-ASSET-EDITOR" },
  "asset-turnaround": { kind: "assets-storyboard", pageId: "CR-010" },
  "asset-expressions": { kind: "assets-storyboard", pageId: "CR-034" },
  prompts: { kind: "assets-storyboard", pageId: "CR-079" },
  shots: { kind: "assets-storyboard", pageId: "M05-SHOT-LIST" },
  "shots-batch": { kind: "assets-storyboard", pageId: "CR-097" },
  "shots-quality": { kind: "assets-storyboard", pageId: "CR-098" },
  "shots-replace": { kind: "assets-storyboard", pageId: "CR-099" },
  "shots-export": { kind: "assets-storyboard", pageId: "CR-100" },
  "shots-import": { kind: "assets-storyboard", pageId: "CR-101" },
  "shots-smart": { kind: "assets-storyboard", pageId: "CR-103" },
  storyboard: { kind: "assets-storyboard", pageId: "CR-104" },
  "shots-compare": { kind: "assets-storyboard", pageId: "CR-102" },
};

export function resolveProjectSurface(slug: string): ProjectSurface | null {
  return PROJECT_SURFACES[slug] ?? null;
}

export function resolveAdminRoute(slug: string) {
  return adminRoutes.find((route) => route.path === `/admin/${slug}`) ?? null;
}
