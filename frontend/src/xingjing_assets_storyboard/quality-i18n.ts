import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  title: "确定性编辑检查",
  scope: "依据镜头文字、时长和资产引用检查。数值问题仅为编辑建议，不阻断冻结；不代表模型能力限制、AI 质量评分或商用合规结论。",
  blockers: "缺场景引用、有对白但缺角色引用会阻断冻结；空故事板不能冻结。无需先生成图片或视频。",
  noKeywords: "未配置额外关键词筛查。正式发布仍需完成独立的内容与授权审核。",
  keywords: "已配置 {{count}} 项关键词筛查；命中将阻断冻结。关键词筛查不能替代正式内容与授权审核。",
  thresholds: "建议镜头时长 {{minimum}}–{{maximum}} 秒，相邻镜头时长差不超过 {{delta}} 秒，文本密度不超过 {{characters}} 字符/秒。文本密度是粗略提示，未计算真实语速，可能受语言影响。",
  evidence: "查看策略版本与摘要",
  unavailable: "服务未返回已配置的检查策略。请重新读取或联系管理员检查生产配置。",
  gateTitle: "冻结前检查",
  gateHelp: "服务端重新校验当前版本；仅阻断问题会阻止冻结，警告可保留。",
  run: "运行质量检测",
  running: "检测中…",
  submissionPending: "上次生成提交尚未确认。重试相同媒体类型会恢复原请求，不另建生成任务。",
  submissionVerification: "生成提交仍在处理或需要核验。请稍后重试恢复原请求；若持续无任务，请联系管理员核对，不要另行提交以免重复计费。",
  submissionRejected: "当前配置或额度未满足生成要求。请修正问题并重新加载，再按服务端状态重试；尚未确认的请求会保留原编号。",
  artifactInvalid: "生成媒体文件缺失或完整性校验未通过，不能采用。请恢复原文件或重新生成。",
  taskNotSucceeded: "生成任务尚未成功，不能采用其输出。",
  freezePlanHelp: "冻结 {{count}} 个镜头的文本、顺序、时长、提示词与资产引用。之后仍可生成和采用媒体，原冻结计划与摘要保持不变。",
  noIssuesTitle: "当前列表未记录质量问题",
  noIssuesHelp: "运行检测可按当前策略重新检查镜头。冻结时服务端仍会重新校验，不把空问题列表视作合规证明。",
  emptyStoryboard: "故事板没有镜头，不能冻结。请先导入或保存分镜计划。",
  qualityBlocked: "存在阻断问题。请补齐场景及对白角色引用，或处理关键词命中后重试。",
};
const en: Record<keyof typeof zh, string> = {
  title: "Deterministic editorial checks",
  scope: "Checks use shot text, duration and asset references. Numeric findings are editing suggestions and do not block freezing. They are not model limits, AI quality scores or commercial compliance approval.",
  blockers: "Missing scene references or dialogue without a character reference block freezing. An empty storyboard cannot be frozen. Generated images or videos are not required.",
  noKeywords: "No additional keyword screening is configured. Publication still requires separate content and rights review.",
  keywords: "{{count}} screening keywords are configured; matches block freezing. Keyword screening does not replace content and rights review.",
  thresholds: "Suggested shot duration: {{minimum}}–{{maximum}} seconds; adjacent duration change: at most {{delta}} seconds; text density: at most {{characters}} characters/second. Text density is a rough, language-dependent hint, not measured speech timing.",
  evidence: "View policy version and digest",
  unavailable: "The service did not return a configured check policy. Reload or ask an administrator to check deployment configuration.",
  gateTitle: "Checks before freezing",
  gateHelp: "The server rechecks the current version. Blocking issues prevent freezing; warnings may remain.",
  run: "Run quality checks",
  running: "Checking…",
  submissionPending: "The previous submission is unconfirmed. Retry the same media type to recover that request without creating another generation task.",
  submissionVerification: "The submission is processing or needs verification. Retry later to recover the original request. If no task appears, ask an administrator to investigate before starting another request.",
  submissionRejected: "The configuration or credit balance does not meet generation requirements. Correct the issue, reload and retry according to the server state. Unconfirmed requests retain their original identity.",
  artifactInvalid: "The generated file is missing or failed its integrity check and cannot be adopted. Restore the original file or generate it again.",
  taskNotSucceeded: "The generation task has not succeeded, so its output cannot be adopted.",
  freezePlanHelp: "Freeze the text, order, durations, prompts and asset references of {{count}} shots. Media generation and adoption remain available while the frozen plan and digest stay unchanged.",
  noIssuesTitle: "No quality issues are recorded in this list",
  noIssuesHelp: "Run checks to apply the current policy. The server rechecks before freezing; an empty issue list is not compliance evidence.",
  emptyStoryboard: "An empty storyboard cannot be frozen. Import or save a shot plan first.",
  qualityBlocked: "Blocking issues remain. Add scene and dialogue character references or resolve keyword matches, then retry.",
};
const vi: Record<keyof typeof zh, string> = {
  title: "Kiểm tra biên tập theo quy tắc",
  scope: "Kiểm tra dựa trên văn bản, thời lượng và tham chiếu tài sản. Các giá trị số chỉ là gợi ý biên tập, không chặn khóa; không phải giới hạn mô hình, điểm chất lượng AI hay kết luận tuân thủ thương mại.",
  blockers: "Thiếu tham chiếu bối cảnh hoặc có lời thoại nhưng thiếu tham chiếu nhân vật sẽ chặn khóa. Không thể khóa bảng phân cảnh trống. Không cần tạo hình ảnh hoặc video trước.",
  noKeywords: "Chưa cấu hình sàng lọc từ khóa bổ sung. Xuất bản vẫn cần xét duyệt nội dung và quyền sử dụng riêng.",
  keywords: "Đã cấu hình {{count}} từ khóa; nếu khớp sẽ chặn khóa. Sàng lọc từ khóa không thay thế xét duyệt nội dung và quyền sử dụng.",
  thresholds: "Thời lượng gợi ý: {{minimum}}–{{maximum}} giây; chênh lệch giữa hai cảnh liền nhau tối đa {{delta}} giây; mật độ văn bản tối đa {{characters}} ký tự/giây. Đây là gợi ý thô phụ thuộc ngôn ngữ, không phải thời gian nói thực tế.",
  evidence: "Xem phiên bản và mã băm chính sách",
  unavailable: "Dịch vụ chưa trả về chính sách kiểm tra đã cấu hình. Hãy tải lại hoặc nhờ quản trị viên kiểm tra cấu hình triển khai.",
  gateTitle: "Kiểm tra trước khi khóa",
  gateHelp: "Máy chủ kiểm tra lại phiên bản hiện tại. Chỉ lỗi chặn mới ngăn khóa; cảnh báo có thể giữ lại.",
  run: "Chạy kiểm tra chất lượng",
  running: "Đang kiểm tra…",
  submissionPending: "Lần gửi trước chưa được xác nhận. Thử lại cùng loại phương tiện để khôi phục yêu cầu cũ mà không tạo tác vụ mới.",
  submissionVerification: "Yêu cầu đang xử lý hoặc cần xác minh. Thử lại sau để khôi phục yêu cầu cũ. Nếu vẫn không có tác vụ, hãy nhờ quản trị viên kiểm tra trước khi gửi yêu cầu khác.",
  submissionRejected: "Cấu hình hoặc số dư chưa đáp ứng yêu cầu tạo. Sửa vấn đề, tải lại và thử theo trạng thái máy chủ. Yêu cầu chưa xác nhận giữ nguyên mã cũ.",
  artifactInvalid: "Tệp đã tạo bị thiếu hoặc không qua kiểm tra tính toàn vẹn nên không thể chọn. Khôi phục tệp gốc hoặc tạo lại.",
  taskNotSucceeded: "Tác vụ tạo chưa thành công nên không thể chọn đầu ra.",
  freezePlanHelp: "Khóa văn bản, thứ tự, thời lượng, lời nhắc và tham chiếu tài sản của {{count}} cảnh. Vẫn có thể tạo và chọn phương tiện; kế hoạch và mã băm đã khóa không đổi.",
  noIssuesTitle: "Danh sách chưa ghi nhận vấn đề chất lượng",
  noIssuesHelp: "Chạy kiểm tra để áp dụng chính sách hiện tại. Máy chủ kiểm tra lại trước khi khóa; danh sách lỗi trống không phải bằng chứng tuân thủ.",
  emptyStoryboard: "Không thể khóa bảng phân cảnh trống. Hãy nhập hoặc lưu kế hoạch cảnh quay trước.",
  qualityBlocked: "Vẫn còn lỗi chặn. Bổ sung tham chiếu bối cảnh và nhân vật có lời thoại hoặc xử lý từ khóa khớp rồi thử lại.",
};
for (const [language, resources] of Object.entries({ zh, en, vi })) {
  i18n.addResourceBundle(language, "storyboardQuality", resources, true, false);
}
export const useStoryboardQualityTranslation = () => useTranslation("storyboardQuality");

export function qualityErrorMessage(code?: string): string | undefined {
  if (code === "SHOT_GENERATION_SUBMISSION_PENDING") return i18n.t("submissionPending", { ns: "storyboardQuality" });
  if (code === "GENERATION_SUBMISSION_VERIFICATION_REQUIRED") return i18n.t("submissionVerification", { ns: "storyboardQuality" });
  if (code === "GENERATED_ASSET_FILE_INVALID") return i18n.t("artifactInvalid", { ns: "storyboardQuality" });
  if (code === "GENERATED_ASSET_TASK_NOT_SUCCEEDED") return i18n.t("taskNotSucceeded", { ns: "storyboardQuality" });
  if (["GENERATION_PROVIDER_SUBMISSION_UNAVAILABLE", "GENERATION_BILLING_POLICY_UNAVAILABLE", "MODEL_ROUTE_CONTROL_UNAVAILABLE", "MODEL_ROUTE_UNAVAILABLE", "MODEL_PROVIDER_ADAPTER_UNAVAILABLE", "GENERATION_INSUFFICIENT_CREDITS", "GENERATION_BILLING_ACCOUNT_MISSING", "GENERATION_BILLING_CURRENCY_MISMATCH", "GENERATION_WORKSPACE_CONCURRENCY_LIMIT"].includes(code ?? "")) return `${i18n.t("submissionRejected", { ns: "storyboardQuality" })} (${code})`;
  if (code === "EMPTY_STORYBOARD") return i18n.t("emptyStoryboard", { ns: "storyboardQuality" });
  if (code === "QUALITY_GATE_FAILED") return i18n.t("qualityBlocked", { ns: "storyboardQuality" });
  if (code === "QUALITY_POLICY_REQUIRED") return i18n.t("unavailable", { ns: "storyboardQuality" });
  return undefined;
}
