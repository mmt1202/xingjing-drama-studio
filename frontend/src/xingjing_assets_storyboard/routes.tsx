import type { ComponentType } from "react";

import { AssetsWorkspace, type AssetsWorkspaceProps } from "./AssetsWorkspace";
import type { AssetsStoryboardPort } from "./contracts";
import { StoryboardWorkspace, type StoryboardWorkspaceProps } from "./StoryboardWorkspace";

export interface AssetsStoryboardRouteProps {
  readonly workspaceId: string;
  readonly projectId?: string;
  readonly episodeId?: string;
  readonly assetId?: string;
  readonly shotId?: string;
  readonly projectName?: string;
  readonly api?: AssetsStoryboardPort;
  readonly onNavigate?: AssetsWorkspaceProps["onNavigate"];
}

function assetProps(props: AssetsStoryboardRouteProps): Pick<AssetsWorkspaceProps, "scope" | "assetId" | "projectName" | "api" | "onNavigate"> {
  return { scope: { workspaceId: props.workspaceId, projectId: props.projectId, episodeId: props.episodeId }, assetId: props.assetId, projectName: props.projectName, api: props.api, onNavigate: props.onNavigate };
}

function shotProps(props: AssetsStoryboardRouteProps): Pick<StoryboardWorkspaceProps, "scope" | "shotId" | "projectName" | "api"> {
  return { scope: { workspaceId: props.workspaceId, projectId: props.projectId, episodeId: props.episodeId }, shotId: props.shotId, projectName: props.projectName, api: props.api };
}

export const AssetExtractionPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="extract" />;
export const GlobalAssetLibraryPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="library" />;
export const AssetLibraryPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="library" />;
export const AssetEditorPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="editor" />;
export const CharacterTurnaroundPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="turnaround" />;
export const ExpressionSetPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="expressions" />;
export const AssetMarketPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="market" />;
export const AssetMarketDetailPage = (props: AssetsStoryboardRouteProps) => <AssetsWorkspace {...assetProps(props)} view="market-detail" />;

export const PromptCenterPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="prompts" />;
export const ShotListPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="list" />;
export const ShotBatchEditPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="batch" />;
export const ShotQualityCheckPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="quality" />;
export const ShotReplacePage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="replace" />;
export const ShotTableExportPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="export" />;
export const ShotTableImportPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="import" />;
export const SmartStoryboardPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="smart" />;
export const StoryboardPage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="storyboard" />;
export const ShotVersionComparePage = (props: AssetsStoryboardRouteProps) => <StoryboardWorkspace {...shotProps(props)} view="compare" />;

export interface AssetsStoryboardRouteExport {
  readonly pageId: string;
  readonly path: string;
  readonly component: ComponentType<AssetsStoryboardRouteProps>;
}

export const assetsStoryboardRouteExports: readonly AssetsStoryboardRouteExport[] = [
  { pageId: "CR-045", path: "/workspaces/:workspaceId/assets", component: GlobalAssetLibraryPage },
  { pageId: "CR-027", path: "/workspaces/:workspaceId/projects/:projectId/assets/extract", component: AssetExtractionPage },
  { pageId: "M04-ASSET-LIST", path: "/workspaces/:workspaceId/projects/:projectId/assets", component: AssetLibraryPage },
  { pageId: "CR-010", path: "/workspaces/:workspaceId/projects/:projectId/assets/:assetId/turnaround", component: CharacterTurnaroundPage },
  { pageId: "CR-034", path: "/workspaces/:workspaceId/projects/:projectId/assets/:assetId/expressions", component: ExpressionSetPage },
  { pageId: "M04-ASSET-EDITOR", path: "/workspaces/:workspaceId/projects/:projectId/assets/:assetId", component: AssetEditorPage },
  { pageId: "CR-006", path: "/workspaces/:workspaceId/asset-market", component: AssetMarketPage },
  { pageId: "CR-005", path: "/workspaces/:workspaceId/asset-market/:assetId", component: AssetMarketDetailPage },
  { pageId: "CR-079", path: "/workspaces/:workspaceId/projects/:projectId/prompts", component: PromptCenterPage },
  { pageId: "M05-SHOT-LIST", path: "/workspaces/:workspaceId/projects/:projectId/shots", component: ShotListPage },
  { pageId: "CR-097", path: "/workspaces/:workspaceId/projects/:projectId/shots/batch", component: ShotBatchEditPage },
  { pageId: "CR-098", path: "/workspaces/:workspaceId/projects/:projectId/shots/quality", component: ShotQualityCheckPage },
  { pageId: "CR-099", path: "/workspaces/:workspaceId/projects/:projectId/shots/replace", component: ShotReplacePage },
  { pageId: "CR-100", path: "/workspaces/:workspaceId/projects/:projectId/shots/export", component: ShotTableExportPage },
  { pageId: "CR-101", path: "/workspaces/:workspaceId/projects/:projectId/shots/import", component: ShotTableImportPage },
  { pageId: "CR-103", path: "/workspaces/:workspaceId/projects/:projectId/shots/smart", component: SmartStoryboardPage },
  { pageId: "CR-104", path: "/workspaces/:workspaceId/projects/:projectId/storyboard", component: StoryboardPage },
  { pageId: "CR-102", path: "/workspaces/:workspaceId/projects/:projectId/shots/compare", component: ShotVersionComparePage },
];
