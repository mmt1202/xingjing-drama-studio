import type { AdminDomain, AdminRouteDefinition } from "./types";

type Seed = [string, string, string, AdminDomain, string, string, string, string];
const seeds: Seed[] = [
  ["AD-017","admin-home","平台首页","business","dashboard","admin.business","运营指标","保存配置"],
  ["AD-050","admin-users","用户管理","business","users","admin.business","用户","恢复用户"],
  ["AD-048","admin-teams","团队管理","business","teams","admin.business","团队","更新团队"],
  ["AD-030","admin-projects","项目管理","business","projects","admin.business","项目","冻结项目"],
  ["AD-011","admin-content","内容管理","business","content","admin.business","内容","执行治理"],
  ["AD-047","admin-tasks","生成任务","business","tasks","admin.business","任务","执行补偿"],
  ["AD-023","admin-models","模型管理","models","models","admin.model","模型","更新模型"],
  ["AD-021","admin-model-callback-log","模型回调日志","models","callback-logs","admin.model","回调日志","重新校验"],
  ["AD-022","admin-model-quality-dashboard","模型质量看板","models","quality","admin.model","质量记录","刷新统计"],
  ["AD-004","admin-billing","算力计费","finance","billing","admin.finance","账务记录","发起调账"],
  ["AD-012","admin-cost-dashboard","成本看板","finance","costs","admin.finance","成本记录","导出对账"],
  ["AD-013","admin-entitlements","权益管理","finance","entitlements","admin.finance","权益","更新权益"],
  ["AD-018","admin-invoice","发票管理","finance","invoices","admin.finance","发票","执行审批"],
  ["AD-026","admin-orders","订单管理","finance","orders","admin.finance","订单","更新订单"],
  ["AD-027","admin-plan-detail","套餐详情","finance","plan-detail","admin.finance","套餐明细","保存套餐"],
  ["AD-028","admin-plans","套餐管理","finance","plans","admin.finance","套餐","发布套餐"],
  ["AD-033","admin-reconciliation","财务对账","finance","reconciliation","admin.finance","对账记录","确认对账"],
  ["AD-034","admin-refund","退款管理","finance","refunds","admin.finance","退款单","执行退款审批"],
  ["AD-035","admin-revenue-dashboard","收入看板","finance","revenue","admin.finance","收入记录","导出收入"],
  ["AD-036","admin-review","审核中心","review","reviews","admin.compliance","审核记录","执行审核"],
  ["AD-009","admin-compliance-dashboard","合规风险看板","review","risks","admin.compliance","风险记录","发起复核"],
  ["AD-010","admin-compliance","合规管理","review","compliance","admin.compliance","合规记录","执行放行"],
  ["AD-003","admin-audit-log","平台审计日志","security","audit-logs","admin.security","审计记录","导出审计"],
  ["AD-019","admin-login-log","后台登录日志","security","login-logs","admin.security","登录记录","标记风险"],
  ["AD-037","admin-role-permissions","后台角色权限","security","roles","admin.security","角色","保存权限"],
  ["AD-038","admin-sensitive-operation","敏感操作审批","security","approvals","admin.security","审批记录","执行审批"],
  ["AD-039","admin-sensitive-word-rules","敏感词规则","review","word-rules","admin.compliance","规则","发布规则"],
  ["AD-041","admin-staff","后台员工管理","security","staff","admin.security","员工","更新员工"],
  ["AD-044","admin-system-announcement","系统公告","security","announcements","admin.security","公告","发布公告"],
  ["AD-045","admin-system","系统配置","security","system-config","admin.security","配置版本","发布配置"],
  ["AD-040","admin-service-status","服务状态监控","operations","services","admin.ops","服务记录","执行恢复"],
  ["AD-001","admin-alert-rules","告警规则","operations","alert-rules","admin.ops","告警规则","发布规则"],
  ["AD-014","admin-error-log","异常日志","operations","error-logs","admin.ops","异常日志","创建工单"],
  ["AD-016","admin-growth-dashboard","增长数据看板","operationsConfig","growth","admin.business","增长记录","刷新统计"],
  ["AD-024","admin-notifications","通知中心","notifications","notifications","admin.notification","通知","发送通知"],
  ["AD-025","admin-ops","运营配置","operationsConfig","ops-config","admin.business","配置版本","发布配置"],
  ["AD-029","admin-platform-profile","平台档案配置","operationsConfig","platform-profiles","admin.business","平台档案","发布档案"],
  ["AD-031","admin-publish-rules","发布规则管理","operationsConfig","publish-rules","admin.business","发布规则","发布规则"],
  ["AD-032","admin-queue-monitor","队列监控","operations","queues","admin.ops","队列记录","执行恢复"],
  ["AD-042","admin-storage-monitor","存储监控","operations","storage","admin.ops","存储记录","执行清理"],
  ["AD-043","admin-support","客服支持","support","tickets","admin.support","工单记录","处理工单"],
  ["AD-046","admin-task-notice-rules","任务通知规则","notifications","task-notices","admin.notification","通知规则","发布规则"],
  ["AD-002","admin-api","开放 API","api","api-clients","admin.api","API 客户端","创建 API Key"],
];

export const adminRoutes: AdminRouteDefinition[] = seeds.map(([id, slug, title, domain, resource, permission, emptyLabel, primaryAction]) => ({
  id, path: `/admin/${slug}`, title, domain, resource, viewPermission: `${permission}.view`, managePermission: `${permission}.manage`, emptyLabel, primaryAction,
}));

export const adminRouteMap = new Map(adminRoutes.map((route) => [route.id, route]));
export const adminPlatformRouteMounts = adminRoutes.map(({ id, path }) => ({ id, path }));
