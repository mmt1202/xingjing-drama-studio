import i18n from "@/i18n";

import { collaborationCommerceRoutes } from "./routes";

export const collaborationCommerceNamespace = "xingjingCollaborationCommerce";

const zhTitles: Record<string, string> = {
  "CR-035": "外链权限设置", "CR-069": "操作日志", "CR-070": "权限矩阵", "CR-075": "项目成员",
  "CR-077": "项目权限详情", "CR-107": "团队空间", "CR-123": "工作区权限",
  "TM-006": "企业认证", "TM-007": "企业配置", "TM-008": "邀请记录", "TM-009": "成员绩效",
  "TM-010": "团队成员", "TM-011": "操作日志", "TM-012": "团队概览", "TM-013": "权限矩阵",
  "TM-016": "角色管理", "TM-017": "席位管理", "TM-018": "团队空间", "TM-001": "团队账单",
  "TM-004": "项目成本", "TM-005": "算力流水", "TM-014": "套餐详情", "TM-015": "项目成本明细",
  "CL-001": "客户验收", "CL-002": "客户批注", "CL-003": "客户审片", "CL-004": "访问验证",
  "CL-005": "客户审片播放器", "CR-011": "客户审片链接", "CR-085": "审片交付", "TM-002": "客户链接管理",
  "TM-003": "团队审片", "AD-015": "导出模板管理", "AD-020": "消息模板管理", "AD-049": "模板审核",
  "CR-108": "提交模板审核", "CR-109": "创建模板", "CR-110": "模板详情", "CR-024": "创作者社区",
  "CR-040": "Fork 授权", "CR-041": "Fork 血缘", "CR-043": "Fork 收益分成", "CR-111": "模板市场",
  "CR-112": "模板 Fork", "AD-005": "商单详情管理", "AD-006": "商单争议处理", "AD-007": "商单结算审核",
  "AD-008": "商单管理", "CR-012": "交付验收", "CR-013": "商单交付", "CR-014": "商单详情",
  "CR-015": "商单列表", "CR-016": "商单结算",
  "CR-009": "算力账单",
};

const enTitles: Record<string, string> = {
  "CR-035": "External link permissions", "CR-069": "Operation log", "CR-070": "Permission matrix", "CR-075": "Project members",
  "CR-077": "Project permission details", "CR-107": "Team workspace", "CR-123": "Workspace permissions",
  "TM-006": "Enterprise verification", "TM-007": "Enterprise settings", "TM-008": "Invitation history", "TM-009": "Member performance",
  "TM-010": "Team members", "TM-011": "Audit log", "TM-012": "Team overview", "TM-013": "Permission matrix",
  "TM-016": "Role management", "TM-017": "Seat management", "TM-018": "Team workspace", "TM-001": "Team billing",
  "TM-004": "Project costs", "TM-005": "Compute ledger", "TM-014": "Plan details", "TM-015": "Project cost details",
  "CL-001": "Client acceptance", "CL-002": "Client comments", "CL-003": "Client review", "CL-004": "Access verification",
  "CL-005": "Client review player", "CR-011": "Client review links", "CR-085": "Review delivery", "TM-002": "Client link management",
  "TM-003": "Team reviews", "AD-015": "Export templates", "AD-020": "Message templates", "AD-049": "Template moderation",
  "CR-108": "Submit template", "CR-109": "Create template", "CR-110": "Template details", "CR-024": "Creator community",
  "CR-040": "Fork licensing", "CR-041": "Fork lineage", "CR-043": "Fork revenue share", "CR-111": "Template marketplace",
  "CR-112": "Fork template", "AD-005": "Commercial order administration", "AD-006": "Dispute resolution", "AD-007": "Settlement approval",
  "AD-008": "Commercial orders", "CR-012": "Delivery acceptance", "CR-013": "Commercial delivery", "CR-014": "Commercial order details",
  "CR-015": "Commercial orders", "CR-016": "Commercial settlement",
  "CR-009": "Compute billing",
};

const viTitles: Record<string, string> = {
  "CR-035": "Quyền liên kết ngoài", "CR-069": "Nhật ký thao tác", "CR-070": "Ma trận quyền", "CR-075": "Thành viên dự án",
  "CR-077": "Chi tiết quyền dự án", "CR-107": "Không gian nhóm", "CR-123": "Quyền không gian làm việc",
  "TM-006": "Xác minh doanh nghiệp", "TM-007": "Cấu hình doanh nghiệp", "TM-008": "Lịch sử lời mời", "TM-009": "Hiệu suất thành viên",
  "TM-010": "Thành viên nhóm", "TM-011": "Nhật ký thao tác", "TM-012": "Tổng quan nhóm", "TM-013": "Ma trận quyền",
  "TM-016": "Quản lý vai trò", "TM-017": "Quản lý chỗ ngồi", "TM-018": "Không gian nhóm", "TM-001": "Hóa đơn nhóm",
  "TM-004": "Chi phí dự án", "TM-005": "Sổ cái tính toán", "TM-014": "Chi tiết gói", "TM-015": "Chi tiết chi phí dự án",
  "CL-001": "Khách hàng nghiệm thu", "CL-002": "Bình luận khách hàng", "CL-003": "Khách hàng duyệt phim", "CL-004": "Xác minh truy cập",
  "CL-005": "Trình phát duyệt phim", "CR-011": "Liên kết duyệt phim", "CR-085": "Bàn giao duyệt phim", "TM-002": "Quản lý liên kết khách hàng",
  "TM-003": "Duyệt phim nhóm", "AD-015": "Mẫu xuất", "AD-020": "Mẫu tin nhắn", "AD-049": "Duyệt mẫu",
  "CR-108": "Gửi duyệt mẫu", "CR-109": "Tạo mẫu", "CR-110": "Chi tiết mẫu", "CR-024": "Cộng đồng nhà sáng tạo",
  "CR-040": "Ủy quyền Fork", "CR-041": "Dòng dõi Fork", "CR-043": "Chia sẻ doanh thu Fork", "CR-111": "Chợ mẫu",
  "CR-112": "Fork mẫu", "AD-005": "Quản trị đơn thương mại", "AD-006": "Xử lý tranh chấp", "AD-007": "Duyệt quyết toán",
  "AD-008": "Quản lý đơn thương mại", "CR-012": "Nghiệm thu bàn giao", "CR-013": "Bàn giao thương mại", "CR-014": "Chi tiết đơn thương mại",
  "CR-015": "Danh sách đơn thương mại", "CR-016": "Quyết toán thương mại",
  "CR-009": "Hóa đơn tài nguyên tính toán",
};

const moduleDescriptions = {
  zh: { M01: "以服务端身份、工作区和权限范围管理账号与协作入口。", M10: "以服务端权限与数据范围治理成员、角色和企业空间。", M11: "核对账单、算力、成本、套餐与对账证据。", M12: "围绕受控版本完成分享、批注、打回与验收。", M13: "管理模板从创建、审核到授权与 Fork 的完整链路。", M14: "跟踪商单报价、合同、里程碑、交付、争议与结算。", M16: "查看工作区算力余额、冻结额、消费流水和项目成本。" },
  en: { M01: "Manage account and collaboration access through server identity, workspace and permission scopes.", M10: "Govern members, roles and enterprise settings within server-authorized scopes.", M11: "Reconcile billing, compute usage, costs, plans and supporting evidence.", M12: "Share, comment on and accept controlled media versions.", M13: "Manage templates from creation and moderation through licensing and forks.", M14: "Track quotes, contracts, milestones, delivery, disputes and settlement.", M16: "Review workspace compute balances, holds, transactions and project costs." },
  vi: { M01: "Quản lý tài khoản và cộng tác bằng danh tính, không gian và quyền phía máy chủ.", M10: "Quản trị thành viên, vai trò và doanh nghiệp theo phạm vi máy chủ.", M11: "Đối soát hóa đơn, tính toán, chi phí, gói và bằng chứng.", M12: "Chia sẻ, bình luận và nghiệm thu phiên bản được kiểm soát.", M13: "Quản lý mẫu từ tạo, duyệt đến cấp phép và Fork.", M14: "Theo dõi báo giá, hợp đồng, cột mốc, bàn giao, tranh chấp và quyết toán.", M16: "Xem số dư, khoản giữ, giao dịch và chi phí dự án của không gian làm việc." },
} as const;

function pageResources(language: keyof typeof moduleDescriptions, titles: Record<string, string>): Record<string, { title: string; description: string; empty: string }> {
  return Object.fromEntries(collaborationCommerceRoutes.map((route) => [route.id, {
    title: titles[route.id] ?? route.id,
    description: moduleDescriptions[language][route.module],
    empty: language === "zh"
      ? (route.id === "TM-001" ? "当前数据范围内还没有团队账单数据" : `当前数据范围内还没有${titles[route.id] ?? "相关"}数据`)
      : language === "en" ? `No ${titles[route.id] ?? "matching"} data exists in the current scope.` : `Chưa có dữ liệu ${titles[route.id] ?? "phù hợp"} trong phạm vi hiện tại.`,
  }]));
}

const actionsZh: Record<string, string> = {
  saveEnterpriseProfile: "保存企业配置", revokeInvitation: "撤销邀请", resendInvitation: "重发邀请", inviteMember: "邀请成员",
  updateRole: "更新角色", disableMember: "停用成员", removeMember: "移除成员", previewPermissionImpact: "预览权限影响", savePermissionMatrix: "保存权限矩阵",
  assignProjectMember: "保存项目成员权限",
  assignSeat: "分配席位", reclaimSeat: "回收席位", requestInvoice: "申请开票", createPaymentOrder: "创建充值订单", refundPaymentOrder: "发起退款", requestReconciliation: "发起对账",
  approve: "通过验收", reject: "打回修改", comment: "提交批注", verify: "验证访问", createReviewLink: "创建审片链接",
  revokeReviewLink: "撤销审片链接", publishTemplate: "发布模板", approveTemplate: "通过模板审核", unpublishTemplate: "下架模板",
  submitTemplateReview: "提交模板审核", createTemplate: "创建模板", applyTemplate: "使用模板", createFork: "创建 Fork",
  saveForkAuthorization: "保存 Fork 授权", saveRevenueShare: "保存收益分成", viewCommercialOrder: "查看商单详情",
  resolveDispute: "处理争议", approveSettlement: "审核结算", updateCommercialOrder: "更新商单", acceptDelivery: "验收通过",
  rejectDelivery: "打回交付", deliver: "提交交付", submitQuote: "提交报价", signContract: "确认合同",
  updateMilestone: "更新里程碑", requestSettlement: "申请结算", openDispute: "发起争议", acceptQuote: "接受报价", recordContract: "登记合同", freezeSettlement: "冻结结算", resumeSettlement: "恢复结算", paySettlement: "支付结算", retryFailed: "仅重试失败项",
};

const actionsEn = Object.fromEntries(Object.keys(actionsZh).map((key) => [key, key.replace(/([A-Z])/g, " $1").replace(/^./, (value) => value.toUpperCase())]));
const actionsVi = { ...actionsEn };

const commonZh = {
  module: "业务模块 {{module}}", context: "工作上下文", requestId: "请求编号：{{id}}", serverTime: "服务端状态时间：{{time}}",
  checking: "正在核对会话、权限与数据范围", loading: "正在读取服务端状态", retry: "重新读取服务端状态", refresh: "读取最新版本",
  denied: { M01: "无权访问此工作区协作能力", M10: "无权访问此团队能力", M11: "无权访问此财务能力", M12: "无权访问此审片能力", M13: "无权访问此模板能力", M14: "无权访问此商单能力", M16: "无权访问此算力账单" },
  unavailable: { M01: "账号与工作区数据暂时不可用", M10: "团队治理数据暂时不可用", M11: "团队财务数据暂时不可用", M12: "审片数据暂时不可用", M13: "模板数据暂时不可用", M14: "商单数据暂时不可用", M16: "算力账单数据暂时不可用" },
  deniedHelp: "请联系工作区管理员调整角色或数据范围后重试。", clientContext: "客户访问上下文仅允许播放、批注与验收",
  searchPlaceholder: "按服务端字段检索", search: "检索", clear: "清除", export: "导出 CSV", exporting: "正在导出", nextPage: "下一页", details: "对象详情", evidence: "状态证据轨",
  noWorkflow: "服务端尚未返回流程证据", related: "关联数据", actions: "可执行操作", selectRecord: "选择对象后执行记录操作",
  cancel: "取消", confirmAction: "确认{{action}}", submitAction: "提交{{action}}", pendingAction: "正在提交{{action}}",
  success: "操作已确认，已读取服务端最终状态", processing: "服务端正在处理，最终状态将以重新读取结果为准",
  partial: "部分对象处理失败", failure: "操作未完成", retryFailed: "仅重试失败项", conflict: "数据已被其他协作者更新",
  conflictHelp: "现有内容未被覆盖。读取最新版本后再确认操作。", financialWarning: "该操作会改变金额、权益或结算状态，请核对服务端对象与金额。",
  approvalWarning: "该操作会改变审批、权限或对外可见状态。", dangerWarning: "该操作可能撤销访问或终止流程，提交后以服务端最终状态为准。",
  standardWarning: "提交前请核对对象、数据范围与操作内容。", clientPlayer: "受控媒体", unavailableMedia: "当前受控版本没有可播放媒体。",
  unknown: "—", activeWorkspace: "当前工作区", clientReview: "受控审片链接", recordCount: "{{count}} 个服务端对象", status: "状态：{{status}}",
  issuedReviewLink: "新建审片链接", copyLink: "复制链接", accessSecret: "访问密码：{{secret}}",
  affectedMembers: "受影响的在职成员", projectAssignments: "关联项目权限", addedPermissions: "新增权限", removedPermissions: "移除权限", noPermissionChanges: "权限集合没有变化",
};

const commonEn = {
  module: "Business module {{module}}", context: "Working context", requestId: "Request ID: {{id}}", serverTime: "Server state time: {{time}}",
  checking: "Checking session, permissions and data scope", loading: "Loading server state", retry: "Reload server state", refresh: "Load latest version",
  denied: { M01: "You cannot access this workspace collaboration capability", M10: "You cannot access this team capability", M11: "You cannot access this finance capability", M12: "You cannot access this review capability", M13: "You cannot access this template capability", M14: "You cannot access this commercial capability", M16: "You cannot access this compute bill" },
  unavailable: { M01: "Account and workspace data is temporarily unavailable", M10: "Team governance data is temporarily unavailable", M11: "Team finance data is temporarily unavailable", M12: "Review data is temporarily unavailable", M13: "Template data is temporarily unavailable", M14: "Commercial data is temporarily unavailable", M16: "Compute billing data is temporarily unavailable" },
  deniedHelp: "Ask a workspace administrator to adjust your role or data scope, then try again.", clientContext: "Client context permits playback, comments and acceptance only",
  searchPlaceholder: "Search server fields", search: "Search", clear: "Clear", export: "Export CSV", exporting: "Exporting", nextPage: "Next page", details: "Object details", evidence: "State evidence",
  noWorkflow: "No workflow evidence was returned by the server", related: "Related data", actions: "Available actions", selectRecord: "Select an object to run record actions",
  cancel: "Cancel", confirmAction: "Confirm {{action}}", submitAction: "Submit {{action}}", pendingAction: "Submitting {{action}}",
  success: "Action confirmed and final server state loaded", processing: "The server is processing this action; reload for the final state",
  partial: "Some objects failed", failure: "Action was not completed", retryFailed: "Retry failed objects only", conflict: "Another collaborator updated this data",
  conflictHelp: "Existing content was preserved. Load the latest version before confirming again.", financialWarning: "This changes money, entitlements or settlement. Verify the server object and amount.",
  approvalWarning: "This changes approval, permission or external visibility.", dangerWarning: "This may revoke access or stop a workflow. The server remains authoritative.",
  standardWarning: "Verify the object, data scope and action before submitting.", clientPlayer: "Controlled media", unavailableMedia: "No playable media is available for this controlled version.",
  unknown: "—", activeWorkspace: "Active workspace", clientReview: "Controlled review link", recordCount: "{{count}} server objects", status: "Status: {{status}}",
  issuedReviewLink: "Issued review link", copyLink: "Copy link", accessSecret: "Access password: {{secret}}",
  affectedMembers: "Affected active members", projectAssignments: "Project assignments", addedPermissions: "Added permissions", removedPermissions: "Removed permissions", noPermissionChanges: "No permission changes",
};

const commonVi = {
  ...commonEn,
  checking: "Đang kiểm tra phiên, quyền và phạm vi dữ liệu", loading: "Đang đọc trạng thái máy chủ", retry: "Đọc lại trạng thái máy chủ", refresh: "Đọc phiên bản mới nhất",
  clientContext: "Ngữ cảnh khách hàng chỉ cho phép phát, bình luận và nghiệm thu", cancel: "Hủy", search: "Tìm kiếm", clear: "Xóa",
  success: "Đã xác nhận thao tác và đọc trạng thái cuối từ máy chủ", partial: "Một số đối tượng xử lý thất bại", conflict: "Dữ liệu đã được cộng tác viên khác cập nhật",
};

const columnsZh = { member: "成员", role: "角色", workspaceRole: "工作区角色", productionRole: "项目生产角色", permissions: "对象权限", dataScope: "数据范围", seat: "席位", status: "状态", updatedAt: "更新时间", project: "项目", model: "模型/执行器", task: "任务", processing: "处理中任务", failure: "失败任务", plan: "套餐", compute: "剩余算力/配额", businessNumber: "业务编号", amount: "金额", relatedTask: "关联任务", occurredAt: "发生时间", reviewObject: "审片对象", visibleVersion: "可见版本", expiresAt: "失效时间", accessPolicy: "访问策略", approvalStatus: "验收状态", template: "模板", type: "类型", version: "版本", scope: "授权范围", price: "价格", reviewStatus: "审核状态", commercialOrder: "商单", client: "客户", contractor: "承接方", milestone: "里程碑", settlementStatus: "结算状态" };
const fieldsZh = { legalName: "企业法定名称", creditCode: "统一社会信用代码", invoiceTitle: "发票抬头", securityContact: "安全联系人邮箱", dataRetentionDays: "数据保留天数", reason: "操作原因", email: "成员邮箱", memberId: "成员编号", roleId: "角色编号", roleName: "角色名称", productionRole: "项目生产角色", dataScope: "数据范围（每行一个对象范围）", active: "启用项目权限", version: "当前版本（首次为 0）", permissions: "权限集合", amountMinor: "金额（最小货币单位）", orderId: "充值订单编号", orderType: "订单类型", expiresInSeconds: "订单有效秒数", timecodeMs: "时间点（毫秒）", comment: "批注内容", credential: "访问凭证", versionId: "版本编号", projectId: "项目编号", finalVideoVersionId: "成片版本编号", watermarkText: "水印文字", accessSecret: "访问密码", "policy.comment": "允许评论", "policy.approve": "允许验收", "policy.download": "允许下载", expiresAt: "失效时间", rightsStatement: "权利声明", priceMinor: "价格（最小货币单位）", name: "名称", type: "类型", scope: "授权范围", targetWorkspaceId: "目标工作区编号", licenseScope: "许可范围", shareBasisPoints: "分成基点", deliveryNote: "交付说明", milestone: "里程碑", disputeId: "争议编号", resolution: "处理结果", settlementId: "结算编号", amount_minor: "金额（最小货币单位）", currency: "币种", deliveryId: "交付编号", evidence_ref: "验收证据引用", milestoneId: "里程碑编号", artifact_version_id: "交付版本编号", artifact_digest: "交付内容摘要", note: "交付备注", proposal: "报价说明", valid_until: "报价有效期", quoteId: "报价编号", content_ref: "合同内容引用", content_digest: "合同内容摘要", kind: "争议类型" };
const columnsEn = { member: "Member", role: "Role", workspaceRole: "Workspace role", productionRole: "Project production role", permissions: "Object permissions", dataScope: "Data scope", seat: "Seat", status: "Status", updatedAt: "Updated", project: "Projects", model: "Model/runner", task: "Tasks", processing: "Running tasks", failure: "Failed tasks", plan: "Plan", compute: "Compute/quota remaining", businessNumber: "Business number", amount: "Amount", relatedTask: "Related task", occurredAt: "Occurred", reviewObject: "Review object", visibleVersion: "Visible version", expiresAt: "Expires", accessPolicy: "Access policy", approvalStatus: "Acceptance status", template: "Template", type: "Type", version: "Version", scope: "License scope", price: "Price", reviewStatus: "Review status", commercialOrder: "Commercial order", client: "Client", contractor: "Contractor", milestone: "Milestone", settlementStatus: "Settlement status" };
const fieldsEn = { legalName: "Legal entity name", creditCode: "Unified social credit code", invoiceTitle: "Invoice title", securityContact: "Security contact email", dataRetentionDays: "Data retention days", reason: "Reason", email: "Member email", memberId: "Member ID", roleId: "Role ID", roleName: "Role name", productionRole: "Project production role", dataScope: "Data scope (one per line)", active: "Enable project access", version: "Current version (0 for first assignment)", permissions: "Permissions", amountMinor: "Amount (minor units)", orderId: "Top-up order ID", orderType: "Order type", expiresInSeconds: "Order expiry (seconds)", timecodeMs: "Timecode (ms)", comment: "Comment", credential: "Access credential", versionId: "Version ID", projectId: "Project ID", finalVideoVersionId: "Final video version ID", watermarkText: "Watermark text", accessSecret: "Access password", "policy.comment": "Allow comments", "policy.approve": "Allow approval", "policy.download": "Allow download", expiresAt: "Expiration time", rightsStatement: "Rights statement", priceMinor: "Price (minor units)", name: "Name", type: "Type", scope: "License scope", targetWorkspaceId: "Target workspace ID", licenseScope: "License scope", shareBasisPoints: "Revenue-share basis points", deliveryNote: "Delivery note", milestone: "Milestone", disputeId: "Dispute ID", resolution: "Resolution", settlementId: "Settlement ID", amount_minor: "Amount (minor units)", currency: "Currency", deliveryId: "Delivery ID", evidence_ref: "Acceptance evidence reference", milestoneId: "Milestone ID", artifact_version_id: "Artifact version ID", artifact_digest: "Artifact digest", note: "Delivery note", proposal: "Proposal", valid_until: "Valid until", quoteId: "Quote ID", content_ref: "Contract content reference", content_digest: "Contract content digest", kind: "Dispute type" };
const columnsVi = { member: "Thành viên", role: "Vai trò", dataScope: "Phạm vi dữ liệu", seat: "Chỗ ngồi", status: "Trạng thái", updatedAt: "Cập nhật", project: "Dự án", model: "Mô hình/trình chạy", task: "Tác vụ", processing: "Tác vụ đang chạy", failure: "Tác vụ lỗi", plan: "Gói", compute: "Hạn mức còn lại", businessNumber: "Số nghiệp vụ", amount: "Số tiền", relatedTask: "Tác vụ liên quan", occurredAt: "Thời điểm", reviewObject: "Đối tượng duyệt", visibleVersion: "Phiên bản hiển thị", expiresAt: "Hết hạn", accessPolicy: "Chính sách truy cập", approvalStatus: "Trạng thái nghiệm thu", template: "Mẫu", type: "Loại", version: "Phiên bản", scope: "Phạm vi cấp phép", price: "Giá", reviewStatus: "Trạng thái duyệt", commercialOrder: "Đơn thương mại", client: "Khách hàng", contractor: "Bên thực hiện", milestone: "Cột mốc", settlementStatus: "Trạng thái quyết toán" };
const fieldsVi = { ...fieldsEn };

function resource(language: "zh" | "en" | "vi") {
  const isZh = language === "zh";
  const common = language === "zh" ? commonZh : language === "en" ? commonEn : commonVi;
  return {
    common,
    pages: pageResources(language, language === "zh" ? zhTitles : language === "en" ? enTitles : viTitles),
    actions: isZh ? actionsZh : language === "en" ? actionsEn : actionsVi,
    columns: isZh ? columnsZh : language === "en" ? columnsEn : columnsVi,
    fields: isZh ? fieldsZh : language === "en" ? fieldsEn : fieldsVi,
  };
}

let registered = false;

export function ensureCollaborationCommerceI18n(): void {
  if (registered) return;
  i18n.addResourceBundle("zh", collaborationCommerceNamespace, resource("zh"), true, false);
  i18n.addResourceBundle("en", collaborationCommerceNamespace, resource("en"), true, false);
  i18n.addResourceBundle("vi", collaborationCommerceNamespace, resource("vi"), true, false);
  registered = true;
}
