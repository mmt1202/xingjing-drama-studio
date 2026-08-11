// router.tsx — Route definitions for the studio layout

import { useEffect, useMemo, useRef } from "react";
import { Route, Switch, Redirect, useParams } from "wouter";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { StudioLayout } from "@/components/layout";
import { StudioCanvasRouter } from "@/components/canvas/StudioCanvasRouter";
import { ProjectsPage } from "@/components/pages/ProjectsPage";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import { ProjectSettingsPage } from "@/components/pages/ProjectSettingsPage";
import { AssetLibraryPage } from "@/components/pages/AssetLibraryPage";
import { LoginPage } from "@/pages/LoginPage";
import {
  AccountSecurityPage,
  InvitationAcceptancePage,
  PasswordResetConfirmPage,
  PasswordResetRequestPage,
  RegisterPage,
  WorkspaceGovernanceHubPage,
  WorkspaceSelectionPage,
} from "@/xingjing_identity";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ToastOverlay } from "@/components/layout/ToastOverlay";
import { OnboardingTour } from "@/onboarding/OnboardingTour";
import {
  buildDemoProjectData,
  buildDemoScripts,
  DEMO_PROJECT_NAME,
  isDemoProject,
} from "@/onboarding/demo-project";
import { setApiReadOnly } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAuthStore } from "@/stores/auth-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { GenerationWorkbench, createGenerationCenterApi, type GenerationWorkbenchView } from "@/xingjing_generation_center";
import { AdminBusinessRoute, CollaborationBusinessRoute, ProjectBusinessRoute, WorkspaceAssetsRoute, WorkspaceProjectsRoute } from "@/xingjing_route_bridge";
import { errMsg } from "@/utils/async";
import {
  ROUTE_APP,
  ROUTE_APP_ASSETS,
  ROUTE_APP_PROJECTS,
  ROUTE_APP_SETTINGS,
  WORKSPACE_ROUTE_SETTINGS,
} from "@/app-routes";

// ---------------------------------------------------------------------------
// ConfigStatusLoader — 登录后集中拉取一次配置完整性状态
// ---------------------------------------------------------------------------

/**
 * 配置完整性（红点 / 必需设置提醒）的单点加载器，始终挂载在路由根，跨页面导航存活。
 * 单例 store 一次初始化即覆盖所有落地页（首页 / 设置 / 项目），不再依赖某个具体页面
 * 是否在 mount 时拉取。首次失败（如后端尚未就绪）时带界次数退避重试，无需手动刷新页面。
 */
function ConfigStatusLoader() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  useEffect(() => {
    if (!isAuthenticated) return;
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await useConfigStatusStore.getState().fetch();
      if (cancelled) return;
      if (!useConfigStatusStore.getState().initialized && attempts < 5) {
        attempts += 1;
        timer = setTimeout(() => void tick(), 800 * attempts);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isAuthenticated]);

  return null;
}

// ---------------------------------------------------------------------------
// AuthGuard — redirects to /login when not authenticated
// ---------------------------------------------------------------------------

function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore();
  const { t } = useTranslation("common");

  if (isLoading) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex h-screen items-center justify-center gap-2 bg-bg text-[13px] text-text-4"
      >
        <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />
        <span>{t("loading")}</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    // 用 `~` 前缀跳到顶层 /login：AuthGuard 可能渲染在 nest 嵌套路由内
    // （/app/projects/:projectName），此时相对路径会被拼到嵌套 base 之后，
    // 必须用绝对路径才能落到真正的 /login。
    // 带上完整原始 URL（取 window.location，nest 内 useLocation 只是相对路径），
    // 登录成功后据此回跳。
    const from = window.location.pathname + window.location.search + window.location.hash;
    return <Redirect to={`~/login?from=${encodeURIComponent(from)}`} />;
  }

  return <>{children}</>;
}

// ---------------------------------------------------------------------------
// StudioWorkspace — loads project data and renders three-column layout
// ---------------------------------------------------------------------------

function StudioWorkspace() {
  const params = useParams<{ projectName: string }>();
  const projectName = params.projectName ?? null;
  const { setCurrentProject, setProjectDetailLoading } = useProjectsStore();
  const { t } = useTranslation("onboarding");
  // 把 t 通过 ref 暴露给首屏加载的 onError 回调，避免切语言触发 t 重建 → effect
  // 依赖跟着重建 → 真实项目整条加载重跑（下方 effect 依赖里刻意不含 t，理由见下）。
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  // 项目生命周期：清空上一个项目的 assistant 状态，再按项目类型取数据。
  // 依赖里不含 `t` —— 界面语言变化只该重灌演示常量（见下一个 effect），不该让真实项目
  // 整条加载重跑（会先清空 store 闪一次空态，还会连带清掉助手会话状态）。
  useEffect(() => {
    if (!projectName) return;

    const assistantState = useAssistantStore.getState();
    assistantState.setSessions([]);
    assistantState.setCurrentSessionId(null);
    assistantState.resetTimeline();
    assistantState.setSessionStatus(null);
    assistantState.setIsDraftSession(false);

    // 引导演示项目在磁盘上并不存在：数据直接来自前端常量，不发请求，并在这段生命周期内
    // 锁死写请求。数据本身由下一个 effect 灌入。
    if (isDemoProject(projectName)) {
      setApiReadOnly(true);
      setProjectDetailLoading(false);
      return () => {
        setApiReadOnly(false);
        setCurrentProject(null, null);
      };
    }

    // 进真实项目一律解锁，不让上一次演示的闸门残留下来
    setApiReadOnly(false);
    setProjectDetailLoading(true);
    // 先落地项目名（数据置空）：refreshProject 的写入门槛要求 currentProjectName 已等于
    // 目标项目，否则响应落地时会被判成「非当前项目」而丢弃（见 projects-store.ts
    // isCurrentProject 的注释）。这一步同时轮换取消域，上一个项目的在途请求随之作废，
    // 首屏加载与 refreshProject 的其余调用方共用同一取消域。
    setCurrentProject(projectName, null);
    void useProjectsStore
      .getState()
      .refreshProject(projectName, {
        onError: (err) =>
          useAppStore.getState().pushToast(tRef.current("dashboard:project_load_failed", { message: errMsg(err) }), "error"),
      })
      .then((result) => {
        // "cancelled" 代表本轮未同步（项目已被切走、取消域已轮换），loading 状态交由
        // 接管的新一轮自行结算，此处不动共享状态。
        if (result === "cancelled") return;
        setProjectDetailLoading(false);
      });

    return () => {
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

  // 演示数据随界面语言走：`t` 换身份时重灌一遍常量，只影响演示项目
  useEffect(() => {
    if (!projectName || !isDemoProject(projectName)) return;
    setCurrentProject(projectName, buildDemoProjectData(t), buildDemoScripts(t));
  }, [projectName, setCurrentProject, t]);

  return (
    <StudioLayout>
      <StudioCanvasRouter />
    </StudioLayout>
  );
}

function WorkspaceProjectsBusinessPage() {
  const { workspaceId = "" } = useParams<{ workspaceId: string }>();
  return <WorkspaceProjectsRoute workspaceId={workspaceId} />;
}

function WorkspaceAssetsBusinessPage({ surface }: { surface: "assets" | "asset-market" }) {
  const { workspaceId = "", assetId } = useParams<{ workspaceId: string; assetId?: string }>();
  return <WorkspaceAssetsRoute workspaceId={workspaceId} surface={surface} assetId={assetId} />;
}

function ProjectBusinessPage() {
  const { workspaceId = "", projectId = "", surface = "", itemId } = useParams<{ workspaceId: string; projectId: string; surface: string; itemId?: string }>();
  return <ProjectBusinessRoute workspaceId={workspaceId} projectId={projectId} surface={surface} itemId={itemId} />;
}

function GenerationBusinessPage({ view }: { view: GenerationWorkbenchView }) {
  const { workspaceId = "", projectId = "" } = useParams<{ workspaceId: string; projectId: string }>();
  const token = useAuthStore((state) => state.token);
  const api = useMemo(() => createGenerationCenterApi({ getAccessToken: () => token }), [token]);
  return <GenerationWorkbench scope={{ workspaceId, projectId }} view={view} api={api} />;
}

const generationViews: Readonly<Record<string, GenerationWorkbenchView>> = {
  "first-last-video": "first-last-video", "image-edit": "image-edit", inpaint: "inpaint",
  "reference-fusion": "reference-fusion", "image-upscale": "image-upscale",
  "model-center": "model-center", "model-detail": "model-detail", "model-market": "model-market", "model-report": "model-report",
  "video-candidates": "video-candidates", "video-extend": "video-extend", "video-retry": "video-retry",
  "video-stabilize": "video-stabilize", "video-upscale": "video-upscale",
};

function GenerationFeaturePage() {
  const { feature = "" } = useParams<{ feature?: string }>();
  const view = generationViews[feature];
  if (!view) return <main className="grid min-h-full place-items-center bg-[#110f0d] p-8 text-stone-300">未找到此生成能力入口。</main>;
  return <GenerationBusinessPage view={view} />;
}

function AdminBusinessPage() {
  const { workspaceId = "", surface = "" } = useParams<{ workspaceId: string; surface: string }>();
  return <AdminBusinessRoute workspaceId={workspaceId} surface={surface} />;
}

function CollaborationBusinessPage() {
  const { workspaceId, projectId, reviewToken, pageId = "" } = useParams<{ workspaceId?: string; projectId?: string; reviewToken?: string; pageId: string }>();
  return <CollaborationBusinessRoute workspaceId={workspaceId} projectId={projectId} reviewToken={reviewToken} pageId={pageId} />;
}

// ---------------------------------------------------------------------------
// Top-level route tree
// ---------------------------------------------------------------------------

export function AppRoutes() {
  return (
    <>
      <ConfigStatusLoader />
      <OnboardingTour />
      <Switch>
        {/* Login page */}
        <Route path="/login" component={LoginPage} />
        <Route path="/register" component={RegisterPage} />
        <Route path="/password-reset" component={PasswordResetRequestPage} />
        <Route path="/password-reset/confirm" component={PasswordResetConfirmPage} />
        <Route path="/invitation/:invitationId"><AuthGuard><InvitationAcceptancePage /></AuthGuard></Route>
        <Route path="/creator/account-security"><AuthGuard><AccountSecurityPage /></AuthGuard></Route>
        <Route path="/creator/account-cancel"><AuthGuard><AccountSecurityPage /></AuthGuard></Route>
        <Route path="/creator/login-devices"><AuthGuard><AccountSecurityPage /></AuthGuard></Route>
        <Route path="/creator/risk-login-alert"><AuthGuard><AccountSecurityPage /></AuthGuard></Route>
        <Route path="/workspace-select">
          <AuthGuard><WorkspaceSelectionPage /></AuthGuard>
        </Route>
        <Route path="/app/account/security">
          <AuthGuard><AccountSecurityPage /></AuthGuard>
        </Route>
        <Route path="/app/account/governance">
          <AuthGuard><WorkspaceGovernanceHubPage /></AuthGuard>
        </Route>

        {/* Token-scoped customer review pages never receive production editing context. */}
        <Route path="/review/:reviewToken/:pageId"><CollaborationBusinessPage /></Route>

        {/* Root redirects to projects list */}
        <Route path="/">
          <Redirect to="/app/projects" />
        </Route>

        {/* /app and /app/ also redirect to projects list */}
        <Route path={ROUTE_APP}>
          <Redirect to={ROUTE_APP_PROJECTS} />
        </Route>

        {/* Projects list */}
        <Route path={ROUTE_APP_PROJECTS}>
          <AuthGuard>
            <ProjectsPage />
          </AuthGuard>
        </Route>

        {/* System settings */}
        <Route path={ROUTE_APP_SETTINGS}>
          <AuthGuard>
            <SystemConfigPage />
          </AuthGuard>
        </Route>

        {/* Asset library */}
        <Route path={ROUTE_APP_ASSETS}>
          <AuthGuard>
            <AssetLibraryPage />
          </AuthGuard>
        </Route>

        {/* Canonical workspace-scoped business pages. Keep these before the legacy project nest. */}
        <Route path="/app/workspaces/:workspaceId/projects">
          <AuthGuard><WorkspaceProjectsBusinessPage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/asset-market/:assetId">
          <AuthGuard><WorkspaceAssetsBusinessPage surface="asset-market" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/asset-market">
          <AuthGuard><WorkspaceAssetsBusinessPage surface="asset-market" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/assets/:assetId">
          <AuthGuard><WorkspaceAssetsBusinessPage surface="assets" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/assets">
          <AuthGuard><WorkspaceAssetsBusinessPage surface="assets" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/collaboration/:pageId">
          <AuthGuard><CollaborationBusinessPage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation/image-to-video">
          <AuthGuard><GenerationBusinessPage view="image-to-video" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation/text-to-video">
          <AuthGuard><GenerationBusinessPage view="text-to-video" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation/image">
          <AuthGuard><GenerationBusinessPage view="image" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation/video">
          <AuthGuard><GenerationBusinessPage view="video" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation/:feature">
          <AuthGuard><GenerationFeaturePage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/generation">
          <AuthGuard><GenerationBusinessPage view="center" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/tasks">
          <AuthGuard><GenerationBusinessPage view="tasks" /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/:surface/:itemId">
          <AuthGuard><ProjectBusinessPage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/projects/:projectId/:surface">
          <AuthGuard><ProjectBusinessPage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/admin/:surface">
          <AuthGuard><AdminBusinessPage /></AuthGuard>
        </Route>
        <Route path="/app/workspaces/:workspaceId/collaboration/:pageId">
          <AuthGuard><CollaborationBusinessPage /></AuthGuard>
        </Route>

        {/* 演示项目没有可用的项目级设置（后端不存在该项目）——地址栏直达、书签或外部链接
            都可能绕开 GlobalHeader 里已做的重定向，这里在路由层再挡一次，指向全局设置。
            必须排在下面通用的项目设置路由之前，wouter 按声明顺序匹配。 */}
        <Route path={`${ROUTE_APP_PROJECTS}/${DEMO_PROJECT_NAME}/${WORKSPACE_ROUTE_SETTINGS}`}>
          <Redirect to={ROUTE_APP_SETTINGS} />
        </Route>

        {/* Project settings — full-screen, must be before the nested workspace route */}
        <Route path={`${ROUTE_APP_PROJECTS}/:projectName/${WORKSPACE_ROUTE_SETTINGS}`}>
          <AuthGuard>
            <ProjectSettingsPage />
          </AuthGuard>
        </Route>

        {/* Studio workspace (three-column layout) */}
        <Route path={`${ROUTE_APP_PROJECTS}/:projectName`} nest>
          <AuthGuard>
            <StudioWorkspace />
          </AuthGuard>
        </Route>

        {/* 404 */}
        <Route>
          <NotFoundPage />
        </Route>
      </Switch>
      <ToastOverlay />
    </>
  );
}
