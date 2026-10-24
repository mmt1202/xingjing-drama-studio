import { paymentButton, usePaymentTranslation } from "@/xingjing_payments/i18n";
import { PaymentsWorkspace } from "@/xingjing_payments/PaymentsWorkspace";
import { VersionWorkspace } from "@/xingjing_versions/VersionWorkspace";
import { useMemo } from "react";
import { EpisodeWorkspace } from "@/xingjing_creator_projects/EpisodeWorkspace";
import { createEpisodesApi } from "@/xingjing_creator_projects/episodes-api";
import { createProjectControlApi } from "@/xingjing_creator_projects/control-api";
import { ProjectFlowShell } from "@/xingjing_creator_projects/ProjectFlowShell";
import { Redirect, useLocation, useSearch } from "wouter";
import { CreatorWorkspaceFrame } from "@/xingjing_creator_home/WorkspaceFrame";
import { CreatorHomePage, WorkspaceTasks } from "@/xingjing_creator_home/CreatorHomePage";
import { ModelOperationsPage } from "@/xingjing_model_operations/ModelOperationsPage";
import { createModelOperationsApi } from "@/xingjing_model_operations/api";
import { CreatorAnalyticsPage } from "@/xingjing_analytics/CreatorAnalyticsPage";
import { CoverWorkspace } from "@/xingjing_cover/CoverWorkspace";
import { CoverReleaseLinks } from "@/xingjing_cover/CoverReleaseLinks";

import { useAuthStore } from "@/stores/auth-store";
import { getTokenForSession } from "@/utils/auth";
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
import { InternalReviewPage } from "@/xingjing_internal_review/InternalReviewPage";
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
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const api = useMemo(
    () => createCreatorProjectsApi({ workspaceId, getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [workspaceId, sessionEpoch],
  );
  return (
    <CreatorWorkspaceFrame workspaceId={workspaceId} active="projects">
    <ProjectLibraryPage
      api={api}
      onNavigate={(target, projectId) => {
        const id = projectId ?? "_new";
        navigate(
          `/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(id)}/${target}`,
        );
      }}
    />
    </CreatorWorkspaceFrame>
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
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const api = useMemo(
    () => createAssetsStoryboardApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
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
    <CreatorWorkspaceFrame workspaceId={workspaceId} active="assets">
    <Page
      workspaceId={workspaceId}
      assetId={assetId}
      api={api}
      onNavigate={go}
    />
    </CreatorWorkspaceFrame>
  );
}

export function WorkspaceLandingRoute({ workspaceId, surface }: { workspaceId: string; surface: string }) {
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const preferenceApi = useMemo(() => createPreferenceApi({ workspaceId, accessToken: getTokenForSession(sessionEpoch) }), [workspaceId, sessionEpoch]);
  const commercePage = { templates: "CR-112", billing: "CR-009", commercial: "CR-013", team: "CR-107" }[surface];
  if (!["home", "tasks", "templates", "billing", "commercial", "team", "settings", "analytics"].includes(surface)) return <MissingSurface />;
  return <CreatorWorkspaceFrame workspaceId={workspaceId} active={surface}>
    {surface === "home" ? <CreatorHomePage /> : surface === "billing" ? <PaymentsWorkspace key={`${workspaceId}:${sessionEpoch}`} workspaceId={workspaceId} /> : surface === "analytics" ? <CreatorAnalyticsPage /> : surface === "tasks" ? <WorkspaceTasks /> : surface === "settings" ? <CreatorSettingsPage pageId="CR-096" api={preferenceApi} /> : commercePage ? <CollaborationBusinessContent workspaceId={workspaceId} pageId={commercePage} /> : null}
  </CreatorWorkspaceFrame>;
}

export function ProjectBusinessRoute({
  workspaceId, projectId, surface, itemId,
}: { workspaceId: string; projectId: string; surface: string; itemId?: string }) {
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const controlApi = useMemo(() => createProjectControlApi(() => getTokenForSession(sessionEpoch)), [sessionEpoch]);
  if (surface === "home") return <Redirect to={`/app/workspaces/${encodeURIComponent(workspaceId)}/home`} />;
  const page = <ProjectBusinessContent workspaceId={workspaceId} projectId={projectId} surface={surface} itemId={itemId} />;
  if (["create", "guided-create"].includes(surface)) return <CreatorWorkspaceFrame workspaceId={workspaceId} active="projects">{page}</CreatorWorkspaceFrame>;
  if (["projects", "home"].includes(surface)) return page;
  return <ProjectFlowShell key={`${workspaceId}:${projectId}:${sessionEpoch}`} api={controlApi} workspaceId={workspaceId} projectId={projectId} slug={surface} itemId={itemId}>{page}</ProjectFlowShell>;
}

function ProjectBusinessContent({
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
  const [, navigate] = useLocation();
  const search = useSearch();
  const surface = resolveProjectSurface(slug, itemId);
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const creatorApi = useMemo(
    () =>
      createCreatorProjectsApi({ workspaceId, getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [workspaceId, sessionEpoch],
  );
  const generationApi = useMemo(
    () => createGenerationAudioApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
  );
  const episodesApi = useMemo(() => createEpisodesApi(() => getTokenForSession(sessionEpoch)), [sessionEpoch]);
  const generationCenterApi = useMemo(
    () => createGenerationCenterApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
  );
  const complianceApi = useMemo(
    () => createComplianceExportApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
  );
  const editingApi = useMemo(
    () => createEditingApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
  );
  const assetsStoryboardApi = useMemo(
    () => createAssetsStoryboardApi({ getAccessToken: () => getTokenForSession(sessionEpoch) }),
    [sessionEpoch],
  );
  const preferenceApi = useMemo(
    () =>
      createPreferenceApi({
        workspaceId,
        accessToken: getTokenForSession(sessionEpoch),
      }),
    [workspaceId, sessionEpoch],
  );
  const complianceQuery = useMemo(
    () => new URLSearchParams(search),
    [search],
  );
  const go = (target: string, nextProjectId = projectId) => {
    const [path, rawQuery = ""] = target.split("?");
    const query = new URLSearchParams(rawQuery);
    const episode = complianceQuery.get("episodeId");
    if (nextProjectId === projectId && episode && !query.has("episodeId")) query.set("episodeId", episode);
    navigate(`/app/workspaces/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(nextProjectId)}/${path}${query.size ? `?${query}` : ""}`);
  };

  if (slug === "collaboration") return <CollaborationBusinessContent workspaceId={workspaceId} projectId={projectId} pageId={itemId ?? "CR-085"} />;
  if (!surface) return <MissingSurface />;
  switch (surface.kind) {
    case "project-library":
      return <WorkspaceProjectsRoute workspaceId={workspaceId} />;
    case "project-create":
      return (
        <ProjectEditorPage
          api={creatorApi}
          mode={surface.mode}
          projectId={projectId === "_new" ? undefined : projectId}
          onDraftSaved={(id) => go(slug, id)}
          onBack={() => navigate(`/app/workspaces/${encodeURIComponent(workspaceId)}/projects`)}
          onCreated={(id) => go("control", id)}
        />
      );
    case "project-control":
      return (
        <ProjectControlPage
          api={creatorApi}
        />
      );
    case "episodes":
      return <EpisodeWorkspace api={episodesApi} creatorApi={creatorApi} projectId={projectId} initialEpisodeId={complianceQuery.has("episodeId") ? Number(complianceQuery.get("episodeId")) : undefined} mode={surface.mode} onNavigate={go} />;
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
        <GenerationWorkbench
          key={complianceQuery.get("taskId") ?? "generation"}
          scope={{ workspaceId, projectId }}
          view={surface.view}
          api={generationCenterApi}
          initialTaskId={complianceQuery.get("taskId") ?? undefined}
        />
      );
    case "audio":
      return (
        <GenerationAudioWorkspace
          workspaceId={workspaceId}
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
          timelineId={itemId ?? complianceQuery.get("timelineId") ?? undefined}
          initialEpisodeId={complianceQuery.get("episodeId") ?? undefined}
          pageId={surface.pageId}
        />
      );
    case "compliance-delivery":
      return (
        <>
        <CoverReleaseLinks projectVersion={complianceQuery.get("projectVersion") ?? undefined} target={complianceQuery.get("target") ?? undefined} selectionId={complianceQuery.get("coverSelectionId") ?? undefined} />
        <ComplianceDeliveryPage
          pageId={surface.pageId}
          projectId={projectId}
          projectVersion={complianceQuery.get("projectVersion") ?? undefined}
          target={complianceQuery.get("target") ?? undefined}
          coverSelectionId={complianceQuery.get("coverSelectionId") ?? undefined}
          api={complianceApi}
        />
        </>
      );
    case "cover":
      return <CoverWorkspace key={`${workspaceId}:${projectId}:${sessionEpoch}`} workspaceId={workspaceId} projectId={projectId} mode={surface.mode} />;
    case "version-history":
      return <VersionWorkspace key={`${workspaceId}:${projectId}:${sessionEpoch}`} workspaceId={workspaceId} projectId={projectId} />;
    case "creator-settings":
      return (
        <>
          <CreatorSettingsPage
            pageId={surface.pageId as CreatorSettingsPageId}
            api={preferenceApi}
            projectId={projectId}
          />
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
          : surface.pageId === "M04-ASSET-EDITOR" ? itemId : undefined;
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
          episodeId={complianceQuery.get("episodeId") ?? undefined}
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
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const [location, navigate] = useLocation();
  const search = useSearch();
  const { t } = usePaymentTranslation();
  const route = resolveAdminRoute(surface);
  const modelApi = useMemo(() => createModelOperationsApi(workspaceId, getTokenForSession(sessionEpoch) ?? ""), [workspaceId, sessionEpoch]);
  const api = useMemo(
    () => createAdminApiClient({ sessionToken: getTokenForSession(sessionEpoch) ?? "", workspaceId }),
    [sessionEpoch, workspaceId],
  );
  if (route?.id === "AD-034" && new URLSearchParams(search).get("legacy") === "compensation") return <section className="xj-payments-page"><div className="xj-payments-notice"><p>{t("legacyCompensationHelp")}</p><button type="button" className={paymentButton} onClick={() => navigate(location)}>{t("backToRefunds")}</button></div><AdminPlatform routeId="AD-034" api={api} /></section>;
  return route?.id === "AD-026" || route?.id === "AD-034" ? <PaymentsWorkspace key={`${workspaceId}:${sessionEpoch}:${route.id}`} workspaceId={workspaceId} initialTab={route.id === "AD-034" ? "refunds" : "orders"} mode="admin" /> : route?.id === "AD-023" ? <ModelOperationsPage key={`${workspaceId}:${sessionEpoch}`} workspaceId={workspaceId} api={modelApi} /> : route?.id === "AD-002" ? (
    <OpenPlatform api={api} />
  ) : route ? (
    <AdminPlatform routeId={route.id} api={api} />
  ) : (
    <MissingSurface />
  );
}

export function CollaborationBusinessRoute({
  workspaceId, projectId, reviewToken, pageId,
}: { workspaceId?: string; projectId?: string; reviewToken?: string; pageId: string }) {
  if (workspaceId && projectId && !reviewToken) return <ProjectBusinessRoute workspaceId={workspaceId} projectId={projectId} surface="collaboration" itemId={pageId} />;
  if (workspaceId && !reviewToken) return <CreatorWorkspaceFrame workspaceId={workspaceId} active={pageId === "CR-009" ? "billing" : ["CR-111", "CR-112"].includes(pageId) ? "templates" : pageId.startsWith("TM-") || pageId === "CR-107" ? "team" : "commercial"}><CollaborationBusinessContent workspaceId={workspaceId} projectId={projectId} pageId={pageId} /></CreatorWorkspaceFrame>;
  return <CollaborationBusinessContent workspaceId={workspaceId} projectId={projectId} reviewToken={reviewToken} pageId={pageId} />;
}

function CollaborationBusinessContent({
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
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const marketplacePages: MarketplacePageId[] = [
    "AD-015", "AD-020", "AD-049", "CR-108", "CR-109", "CR-110",
    "CR-024", "CR-040", "CR-041", "CR-043", "CR-111", "CR-112",
  ];
  const route = collaborationCommerceRouteMap.get(pageId);
  const api = useMemo(
    () => createCollaborationCommerceApi({ accessToken: getTokenForSession(sessionEpoch) ?? undefined }),
    [sessionEpoch],
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
  if (pageId === "CR-009" && workspaceId) return <PaymentsWorkspace key={`${workspaceId}:${sessionEpoch}`} workspaceId={workspaceId} />;
  if (pageId === "CR-085") {
    return <InternalReviewPage workspaceId={workspaceId} projectId={projectId} accessToken={token} />;
  }
  if (["CR-011", "TM-002", "TM-003"].includes(pageId)) {
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
