import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  saveLimits: "保存并应用预算", activatePlans: "核对并启用现有计划预算", explicitSave: "预算变更需要明确保存，不会自动提交。",
  title: "费用归属", help: "选择这次任务所属的剧集，或经授权的项目共有制作费用。素材复用不会重复计费。",
  choose: "请选择费用归属", shared: "项目共有制作", episode: "第 {{number}} 集 · {{title}}",
  loading: "正在核对可选剧集与预算权限…", failed: "暂时无法读取费用归属，请重新检查后再提交。", retry: "重新检查",
  empty: "当前没有可选归属。请联系项目管理员建立剧集，或授权项目共有制作。",
  required: "请选择本次任务的费用归属。", immutable: "任务创建后，费用归属保持固定；重试沿用原归属。",
  projectLimit: "项目预算上限（人民币元）", episodeLimit: "单集预算上限（人民币元）",
  permission: "修改或解除预算上限需要预算管理权限。", limitHelp: "留空表示未设上限，0 表示不允许新增付费任务。已消费和仍在预留的费用不能通过降低预算移除。",
  unlimited: "未设上限", spent: "已消费", reserved: "已预留", remaining: "可再预留", readFailed: "预算使用情况暂不可读。",
  active: "已启用消费限制", planned: "计划预算尚未核验，暂不能视为消费限制。",
  unassigned: "历史未确认归属的费用仍计入项目总额；核验前不能启用单集限制。",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  saveLimits: "Save and apply budgets", activatePlans: "Verify and enable existing planned budgets", explicitSave: "Budget changes require an explicit save and are not submitted automatically.",
  title: "Cost attribution", help: "Choose the episode for this task, or authorized shared project production. Reusing media does not create another charge.",
  choose: "Choose cost attribution", shared: "Shared project production", episode: "Episode {{number}} · {{title}}",
  loading: "Checking episodes and budget permissions…", failed: "Cost attribution could not be loaded. Check again before submitting.", retry: "Check again",
  empty: "No cost scope is available. Ask a project administrator to create episodes or authorize shared project production.",
  required: "Choose this task's cost attribution.", immutable: "Attribution is fixed when the task is created. Retries retain the original scope.",
  projectLimit: "Project budget cap (CNY)", episodeLimit: "Episode budget cap (CNY)",
  permission: "Budget management permission is required to change or remove a cap.", limitHelp: "Blank means no cap. Zero prevents new paid tasks. Lowering the cap cannot remove spent or reserved costs.",
  unlimited: "No cap", spent: "Consumed", reserved: "Reserved", remaining: "Available to reserve", readFailed: "Budget usage could not be loaded.",
  active: "Spending limit enabled", planned: "This planned budget is not verified and is not an active spending limit.",
  unassigned: "Historical costs without verified attribution remain in the project total. Verify them before enabling episode limits.",
};
const vi: Copy = {
  saveLimits: "Lưu và áp dụng ngân sách", activatePlans: "Xác minh và bật ngân sách dự kiến hiện có", explicitSave: "Thay đổi ngân sách cần lưu rõ ràng và không tự động gửi.",
  title: "Phân bổ chi phí", help: "Chọn tập cho tác vụ hoặc chi phí sản xuất dùng chung đã được cấp quyền. Tái sử dụng phương tiện không bị tính phí lần nữa.",
  choose: "Chọn phạm vi chi phí", shared: "Sản xuất dùng chung của dự án", episode: "Tập {{number}} · {{title}}",
  loading: "Đang kiểm tra tập và quyền ngân sách…", failed: "Chưa đọc được phạm vi chi phí. Hãy kiểm tra lại trước khi gửi.", retry: "Kiểm tra lại",
  empty: "Chưa có phạm vi chi phí. Nhờ quản trị viên tạo tập hoặc cấp quyền sản xuất dùng chung.",
  required: "Hãy chọn phạm vi chi phí của tác vụ.", immutable: "Phạm vi được cố định khi tạo tác vụ. Thử lại giữ nguyên phạm vi ban đầu.",
  projectLimit: "Giới hạn ngân sách dự án (CNY)", episodeLimit: "Giới hạn ngân sách tập (CNY)",
  permission: "Cần quyền quản lý ngân sách để thay đổi hoặc bỏ giới hạn.", limitHelp: "Để trống là không giới hạn. Số 0 chặn tác vụ có phí mới. Giảm giới hạn không xóa chi phí đã dùng hoặc đang giữ.",
  unlimited: "Không giới hạn", spent: "Đã dùng", reserved: "Đang giữ", remaining: "Còn có thể giữ", readFailed: "Chưa đọc được mức sử dụng ngân sách.",
  active: "Đã bật giới hạn chi tiêu", planned: "Ngân sách dự kiến chưa được xác minh và chưa phải giới hạn chi tiêu đang áp dụng.",
  unassigned: "Chi phí cũ chưa xác minh phạm vi vẫn nằm trong tổng dự án. Cần xác minh trước khi bật giới hạn tập.",
};
for (const [language, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "productionBudget", copy, true, false);
export const useBudgetTranslation = () => useTranslation("productionBudget");
