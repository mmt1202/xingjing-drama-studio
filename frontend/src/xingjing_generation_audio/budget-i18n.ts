import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  requestChecking: "正在核对原请求，制作输入暂时锁定。",
  requestUncertain: "上次提交结果尚未确认，原请求和费用归属已保留。请核对原请求，避免重复生成；不会自动重发。",
  requestIdentity: "原任务：{{taskId}} · 请求：{{key}}",
  retryOriginal: "核对原请求",
  recoveryUnavailable: "暂时无法安全保存原请求，尚未提交付费任务。请恢复当前浏览器的会话存储后重试。",
  unknown: "供应商是否接收这次请求尚未确认，费用预留继续保留。请联系管理员核验原任务，当前不能重试、取消或释放这笔预留。",
  bindingLoading: "正在核对资源的预算归属…",
  bindingFailed: "暂时无法核对这个资源的预算归属。请确认资源已保存且在当前项目中，然后重新读取。",
  retry: "重新读取归属",
  boundEpisode: "此资源的制作费用已固定归属第 {{episode}} 集，后续任务和重试沿用这一归属。",
  boundShared: "此资源已被授权为项目共有制作费用，后续任务和重试沿用这一归属。",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  requestChecking: "Checking the original request. Production inputs are temporarily locked.",
  requestUncertain: "The previous submission is unconfirmed. Its exact request and cost scope are preserved. Check the original request to avoid duplicate generation. It is never resent automatically.",
  requestIdentity: "Original task: {{taskId}} · Request: {{key}}",
  retryOriginal: "Check original request",
  recoveryUnavailable: "The original request cannot be stored safely, so no paid task was submitted. Restore browser session storage and retry.",
  unknown: "Provider acceptance is unknown, so the cost reservation remains held. Ask an administrator to verify the original task. Retry, cancellation and release are unavailable until verified.",
  bindingLoading: "Checking the resource's budget allocation…",
  bindingFailed: "The resource's allocation could not be checked. Confirm it is saved in this project, then reload.",
  retry: "Reload allocation",
  boundEpisode: "Production costs for this resource belong to episode {{episode}}. New tasks and retries keep this allocation.",
  boundShared: "This resource is approved as a shared project production cost. New tasks and retries keep this allocation.",
};
const vi: Copy = {
  requestChecking: "Đang kiểm tra yêu cầu gốc. Tạm khóa dữ liệu sản xuất.",
  requestUncertain: "Chưa xác nhận lần gửi trước. Đã giữ nguyên yêu cầu và phân bổ chi phí. Kiểm tra yêu cầu gốc để tránh tạo trùng; hệ thống không tự gửi lại.",
  requestIdentity: "Tác vụ gốc: {{taskId}} · Yêu cầu: {{key}}",
  retryOriginal: "Kiểm tra yêu cầu gốc",
  recoveryUnavailable: "Chưa thể lưu yêu cầu gốc an toàn nên chưa gửi tác vụ tính phí. Khôi phục bộ nhớ phiên của trình duyệt rồi thử lại.",
  unknown: "Chưa rõ nhà cung cấp đã nhận yêu cầu nên vẫn giữ khoản dự trữ. Hãy nhờ quản trị viên xác minh tác vụ gốc; chưa thể thử lại, hủy hoặc giải phóng khoản này.",
  bindingLoading: "Đang kiểm tra phân bổ ngân sách của tài nguyên…",
  bindingFailed: "Chưa kiểm tra được phân bổ. Hãy xác nhận tài nguyên đã lưu trong dự án này rồi tải lại.",
  retry: "Tải lại phân bổ",
  boundEpisode: "Chi phí sản xuất của tài nguyên thuộc tập {{episode}}. Tác vụ mới và thử lại giữ nguyên phân bổ này.",
  boundShared: "Tài nguyên được duyệt là chi phí sản xuất chung của dự án. Tác vụ mới và thử lại giữ nguyên phân bổ này.",
};
for (const [language, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "audioBudget", copy, true, false);
export const useAudioBudgetTranslation = () => useTranslation("audioBudget");
