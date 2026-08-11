import { useMemo } from "react";
import { useLocation } from "wouter";

import { useAuthStore } from "@/stores/auth-store";
import {
  AiRewritePage,
  AiDirectorPage,
  DirectorAssetsPage,
  OriginalSourcePage,
  ProjectControlPage,
  ProjectEditorPage,
  ProjectLibraryPage,
  ScriptImportPage,
  ScriptCheckupPage,
  ScriptLabPage,
  ScriptVersionComparePage,
  SourceMappingPage,
  createCreatorProjectsApi,
} from "@/xingjing_creator_projects";
import {
  GenerationAudioWorkspace,
  createGenerationAudioApi,
  type AudioPageId,
} from "@/xingjing_generation_audio";
import {
  GenerationWorkbench,
  createGenerationCenterApi,
} from "@/xingjing_generation_center";
import {
  ComplianceDeliveryPage,
  createComplianceExportApi,
} from "@/xingjing_compliance";
import { AdminPlatform, OpenPlatform, createAdminApiClient } from "@/xingjing_admin_platform";
import {
  M08EditingWorkspace,
  createEditingApi,
} from "@/xingjing_postproduction";
import {
  assetsStoryboardRouteExports,
  createAssetsStoryboardApi,
} from "@/xingjing_assets_storyboard";
import {
  CollaborationCommercePage,
  collaborationCommerceRouteMap,
  createCollaborationCommerceApi,
} from "@/xingjing_collaboration_commerce";
import { PublicReviewPage, StaffReviewPage } from "@/xingjing_review_client";
import { CommercialWorkspace } from "@/xingjing_commercial_workspace";
import {
  MarketplacePage,
  type MarketplacePageId,
} from "@/xingjing_marketplace_client";
import {
  CreatorSettingsPage,
  createPreferenceApi,
  type CreatorSettingsPageId,
} from "@/xingjing_creator_settings";

import { resolveAdminRoute, resolveProjectSurface } from "./route-resolution";

function MissingSurface() {
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1>业务页面不存在</h1>
      <p>该地址未映射到正式需求页面。</p>
    </main>
  );
}

export function WorkspaceProjectsRoute({
  workspaceId,
}: {
  workspaceId: string;
}) {
  const [, navigate] = useLocation();
  const token = useAuthStore((state) => state.token);
  const api = useMemo(
    () => createCreatorProjectsApi({ workspaceId, getAccessToken: () => token }),
    [workspaceId, token],
  );
  return (
    <ProjectLibraryPage
      api={api}
      onNavigate={(target, projectId) => {
        const id = projectId ?? "_new";
        navigate(
          `/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(id)}/${target}`,
        );
      }}
    />
  );
}

export function WorkspaceAssetsRoute({
  workspaceId,
  surface,
  assetId,
}: {
  workspaceId: string;
  surface: "assets" | "asset-market";
  assetId?: string;
}) {
  const [, navigate] = useLocation();
  const token = useAuthStore((state) => state.token);
  const api = useMemo(
    () => createAssetsStoryboardApi({ getAccessToken: () => token }),
    [token],
  );
  const pageId =
    surface === "assets"
      ? assetId
        ? "M04-ASSET-EDITOR"
        : "CR-045"
      : assetId
        ? "CR-005"
        : "CR-006";
  const route = assetsStoryboardRouteExports.find(
    (candidate) => candidate.pageId === pageId,
  );
  if (!route) return <MissingSurface />;
  const Page = route.component;
  const go = (
    view:
      | "extract"
      | "library"
      | "editor"
      | "turnaround"
      | "expressions"
      | "market"
      | "market-detail",
    nextAssetId?: string,
  ) => {
    if (view === "library")
      navigate(`/app/workspaces/${encodeURIComponent(workspaceId)}/assets`);
    else if (view === "editor" && nextAssetId)
      navigate(
        `/app/workspaces/${encodeURIComponent(workspaceId)}/assets/${encodeURIComponent(nextAssetId)}`,
      );
    else if (view === "market")
      navigate(
        `/app/workspaces/${encodeURIComponent(workspaceId)}/asset-market`,
      );
    else if (view === "market-detail" && nextAssetId)
      navigate(
        `/app/workspaces/${encodeURIComponent(workspaceId)}/asset-market/${encodeURIComponent(nextAssetId)}`,
      );
  };
  return (
    <Page
      workspaceId={workspaceId}
      assetId={assetId}
      api={api}
      onNavigate={go}
    />
  );
}

export function ProjectBusinessRoute({
  workspaceId,
  projectId,
  surface: slug,
  itemId,
}: {
  workspaceId: string;
  projectId: string;
  surface: string;
  itemId?: string;
}) {
  const [location, navigate] = useLocation();
  const surface = resolveProjectSurface(slug);
  const token = useAuthStore((state) => state.token);
  const creatorApi = useMemo(
    () =>
      createCreatorProjectsApi({ workspaceId, getAccessToken: () => token }),
    [workspaceId, token],
  );
  const generationApi = useMemo(
    () => createGenerationAudioApi({ getAccessToken: () => token }),
    [token],
  );
  const generationCenterApi = useMemo(
    () => createGenerationCenterApi({ getAccessToken: () => token }),
    [token],
  );
  const complianceApi = useMemo(
    () => createComplianceExportApi({ getAccessToken: () => token }),
    [token],
  );
  const editingApi = useMemo(
    () => createEditingApi({ getAccessToken: () => token }),
    [token],
  );
  const assetsStoryboardApi = useMemo(
    () => createAssetsStoryboardApi({ getAccessToken: () => token }),
    [token],
  );
  const preferenceApi = useMemo(
    () =>
      createPreferenceApi({
        workspaceId,
        accessToken: token,
      }),
    [workspaceId, token],
  );
  const complianceQuery = useMemo(
    () => new URLSearchParams(location.split("?", 2)[1] ?? ""),
    [location],
  );
  const go = (target: string, nextProjectId = projectId) =>
    navigate(
      `/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(nextProjectId)}/${target}`,
    );

  if (!surface) return <MissingSurface />;
  switch (surface.kind) {
    case "project-library":
      return <WorkspaceProjectsRoute workspaceId={workspaceId} />;
    case "project-create":
      return (
        <ProjectEditorPage
          api={creatorApi}
          mode={surface.mode}
          onCreated={(id) => go("control", id)}
        />
      );
    case "project-control":
      return (
        <ProjectControlPage
          api={creatorApi}
          projectId={projectId}
          onNavigate={go}
        />
      );
    case "script-import":
      return <ScriptImportPage api={creatorApi} projectId={projectId} />;
    case "ai-director":
      return <AiDirectorPage api={creatorApi} projectId={projectId} />;
    case "director-assets":
      return <DirectorAssetsPage api={creatorApi} projectId={projectId} />;
    case "script-checkup":
      return <ScriptCheckupPage api={creatorApi} projectId={projectId} />;
    case "script-lab":
      return <ScriptLabPage api={creatorApi} projectId={projectId} />;
    case "source":
      return <OriginalSourcePage api={creatorApi} projectId={projectId} />;
    case "source-mapping":
      return <SourceMappingPage api={creatorApi} projectId={projectId} />;
    case "rewrite":
      return <AiRewritePage api={creatorApi} projectId={projectId} />;
    case "script-versions":
      return (
        <ScriptVersionComparePage api={creatorApi} projectId={projectId} />
      );
    case "generation":
      return (
        <GenerationAudioWorkspace projectId={projectId} api={generationApi} />
      );
    case "audio":
      return (
        <GenerationAudioWorkspace
          projectId={projectId}
          pageId={surface.pageId as AudioPageId}
          api={generationApi}
        />
      );
    case "postproduction":
      return (
        <M08EditingWorkspace
          api={editingApi}
          projectId={projectId}
          timelineId={itemId}
          pageId={surface.pageId}
        />
      );
    case "compliance-delivery":
      return (
        <ComplianceDeliveryPage
          pageId={surface.pageId}
          projectId={projectId}
          projectVersion={complianceQuery.get("projectVersion") ?? undefined}
          target={complianceQuery.get("target") ?? undefined}
          api={complianceApi}
        />
      );
    case "creator-settings":
      return (
        <>
          <CreatorSettingsPage
            pageId={surface.pageId as CreatorSettingsPageId}
            api={preferenceApi}
            projectId={projectId}
          />
          {["CR-022", "CR-023"].includes(surface.pageId) ? (
            <GenerationWorkbench
              scope={{ workspaceId, projectId }}
              view="image"
              api={generationCenterApi}
            />
          ) : null}
          {surface.pageId === "CR-046" ? (
            <WorkspaceProjectsRoute workspaceId={workspaceId} />
          ) : null}
        </>
      );
    case "assets-storyboard": {
      const route = assetsStoryboardRouteExports.find(
        (candidate) => candidate.pageId === surface.pageId,
      );
      if (!route) return <MissingSurface />;
      const Page = route.component;
      const assetId =
        surface.pageId.startsWith("CR-0") &&
        ["CR-010", "CR-034", "CR-005"].includes(surface.pageId)
          ? itemId
          : undefined;
      const shotId = surface.pageId === "CR-099" ? itemId : undefined;
      const navigateAsset = (
        view:
          | "extract"
          | "library"
          | "editor"
          | "turnaround"
          | "expressions"
          | "market"
          | "market-detail",
        nextAssetId?: string,
      ) => {
        const encodedAsset = nextAssetId
          ? `/${encodeURIComponent(nextAssetId)}`
          : "";
        if (view === "extract") go("assets-extract");
        else if (view === "library") go("assets");
        else if (view === "editor") go(`asset-editor${encodedAsset}`);
        else if (view === "turnaround") go(`asset-turnaround${encodedAsset}`);
        else if (view === "expressions") go(`asset-expressions${encodedAsset}`);
        else if (view === "market")
          navigate(
            `/app/workspaces/${encodeURIComponent(workspaceId)}/asset-market`,
          );
        else if (view === "market-detail" && nextAssetId)
          navigate(
            `/app/workspaces/${encodeURIComponent(workspaceId)}/asset-market/${encodeURIComponent(nextAssetId)}`,
          );
      };
      return (
        <Page
          workspaceId={workspaceId}
          projectId={projectId}
          assetId={assetId}
          shotId={shotId}
          api={assetsStoryboardApi}
          onNavigate={navigateAsset}
        />
      );
    }
  }
}

export function AdminBusinessRoute({
  workspaceId,
  surface,
}: {
  workspaceId: string;
  surface: string;
}) {
  const token = useAuthStore((state) => state.token) ?? "";
  const route = resolveAdminRoute(surface);
  const api = useMemo(
    () => createAdminApiClient({ sessionToken: token, workspaceId }),
    [token, workspaceId],
  );
  return route?.id === "AD-002" ? (
    <OpenPlatform api={api} />
  ) : route ? (
    <AdminPlatform routeId={route.id} api={api} />
  ) : (
    <MissingSurface />
  );
}

export function CollaborationBusinessRoute({
  workspaceId,
  projectId,
  reviewToken,
  pageId,
}: {
  workspaceId?: string;
  projectId?: string;
  reviewToken?: string;
  pageId: string;
}) {
  const token = useAuthStore((state) => state.token) ?? undefined;
  const marketplacePages: MarketplacePageId[] = [
    "AD-015", "AD-020", "AD-049", "CR-108", "CR-109", "CR-110",
    "CR-024", "CR-040", "CR-041", "CR-043", "CR-111", "CR-112",
  ];
  const route = collaborationCommerceRouteMap.get(pageId);
  const api = useMemo(
    () => createCollaborationCommerceApi({ accessToken: token }),
    [token],
  );
  if (marketplacePages.includes(pageId as MarketplacePageId)) {
    return <MarketplacePage pageId={pageId as MarketplacePageId} accessToken={token} />;
  }
  if (
    workspaceId &&
    [
      "AD-005",
      "AD-006",
      "AD-007",
      "AD-008",
      "CR-012",
      "CR-013",
      "CR-014",
      "CR-015",
      "CR-016",
    ].includes(pageId)
  ) {
    return (
      <CommercialWorkspace
        pageId={
          pageId as
            | "AD-005"
            | "AD-006"
            | "AD-007"
            | "AD-008"
            | "CR-012"
            | "CR-013"
            | "CR-014"
            | "CR-015"
            | "CR-016"
        }
        workspaceId={workspaceId}
        accessToken={token}
      />
    );
  }
  if (
    reviewToken &&
    ["CL-001", "CL-002", "CL-003", "CL-004", "CL-005"].includes(pageId)
  ) {
    return (
      <PublicReviewPage
        pageId={pageId as "CL-001" | "CL-002" | "CL-003" | "CL-004" | "CL-005"}
        reviewToken={reviewToken}
      />
    );
  }
  if (["CR-011", "CR-085", "TM-002", "TM-003"].includes(pageId)) {
    return (
      <StaffReviewPage
        pageId={pageId as "CR-011" | "CR-085" | "TM-002" | "TM-003"}
        workspaceId={workspaceId}
        projectId={projectId}
        accessToken={token}
      />
    );
  }
  if (!route) return <MissingSurface />;
  const context = { workspaceId, projectId, reviewToken };
  if (route.requiredContext.some((key) => !context[key]))
    return <MissingSurface />;
  return (
    <CollaborationCommercePage pageId={pageId} api={api} context={context} />
  );
}
