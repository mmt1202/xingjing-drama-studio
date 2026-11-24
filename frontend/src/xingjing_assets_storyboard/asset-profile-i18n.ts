import i18n from "@/i18n";
import { useTranslation } from "react-i18next";
import type { AssetType } from "./contracts";

export const profileDetails = {
  character: ["appearance", "personality", "speakingStyle"],
  scene: ["environment", "lighting", "timeOfDay"],
  prop: ["purpose", "material", "continuity"],
  costume: ["setting", "material", "continuity"],
  voice: ["timbre", "language", "emotion"],
} as const satisfies Record<AssetType, readonly string[]>;

export function profileLabel(type: AssetType, key: string) {
  return type === "costume" && key === "material" ? "fabric" : key;
}

const zh = {
  name: "资产名称", description: "资产说明", appearance: "角色外观", personality: "角色性格", speakingStyle: "说话方式",
  environment: "场景环境", lighting: "场景光线", timeOfDay: "场景时段", purpose: "道具用途", material: "道具材质", fabric: "服装面料",
  continuity: "镜头间要保持的细节", setting: "服装设定", timbre: "声音音色", language: "声音语言", emotion: "情绪范围",
  title: "用 AI 写初稿", intro: "有对应剧本时会参考原文。看过建议后，你来决定哪些写进资料。",
  generate: "帮我起草", again: "换一版建议", working: "正在起草…", direction: "告诉 AI 你的想法（选填）",
  directionHint: "例如，希望画面保留年代感，光线更柔和",
  review: "选择要填入的建议", fromScript: "已参考这项资产对应的剧本片段。", fromAsset: "本次依据资产名称、已有设定和你的创作想法起草。",
  reviewHint: "已为你勾选空白项。勾选有内容的字段，会替换该项文字。",
  current: "当前内容", empty: "尚未填写", apply: "填入所选 {{count}} 项", dismiss: "收起建议", same: "现有设定与建议一致，可以直接继续编辑。",
  applied: "已填入 {{count}} 项。可以继续修改，满意后点「保存资料」。",
  form: "基本资料", saving: "正在保存…", save: "保存资料", unsaved: "有未保存的修改",
  fail: "这次没能生成建议。你可以重试，也可以继续填写下方设定。", progress: "上一次的建议还在生成中，稍后重试即可取回。",
  denied: "当前账号无法为这项资产生成建议。", stale: "这项资产已有新版本。请先处理当前修改，再重新打开资产获取建议。",
  invalid: "填写的内容较长，请缩短创作想法或设定后再试。", voiceLink: "关联声音（选填）", characterLink: "关联角色（选填）",
  linksHint: "从项目已有资产中选择。", choose: "选择资产", none: "暂不关联", existing: "已关联的资产", remove: "移除关联",
  more: "加载更多", loading: "正在读取可选资产…", noLinks: "项目里还没有此类资产。", linkFailed: "暂时无法读取可选资产。", retry: "重试",
};
const en: typeof zh = {
  name: "Asset name", description: "Description", appearance: "Appearance", personality: "Personality", speakingStyle: "Speaking style",
  environment: "Setting", lighting: "Lighting", timeOfDay: "Time of day", purpose: "Prop use", material: "Prop material", fabric: "Fabric",
  continuity: "Details to keep between shots", setting: "Costume design", timbre: "Timbre", language: "Language and accent", emotion: "Emotional range",
  title: "Draft with AI", intro: "AI can use a linked script. Review the suggestions and choose what to add to this profile.",
  generate: "Generate suggestions", again: "Generate again", working: "Drafting suggestions…", direction: "Add creative direction (optional)",
  directionHint: "For example, keep the period details and soften the lighting",
  review: "Choose suggestions to use", fromScript: "Based on the script excerpt linked to this asset.", fromAsset: "Based on the asset name, current details and your direction.",
  reviewHint: "Empty fields are selected. Selecting a filled field will replace its current text.",
  current: "Current text", empty: "Empty", apply: "Apply {{count}} suggestions", dismiss: "Dismiss suggestions", same: "The suggestions match your current details. You can continue editing.",
  applied: "Applied {{count}} suggestions. Make any edits, then select Save profile.",
  form: "Profile details", saving: "Saving…", save: "Save profile", unsaved: "Unsaved changes",
  fail: "Suggestions could not be generated. Try again or continue editing below.", progress: "Your previous request is still running. Try again shortly to retrieve it.",
  denied: "Your account cannot generate suggestions for this asset.", stale: "A newer asset version is available. Finish your edits and reopen the asset before requesting suggestions.",
  invalid: "The input is too long. Shorten the direction or asset details and try again.", voiceLink: "Linked voice (optional)", characterLink: "Linked characters (optional)",
  linksHint: "Choose from existing project assets.", choose: "Choose an asset", none: "No link", existing: "Previously linked asset", remove: "Remove link",
  more: "Load more", loading: "Loading available assets…", noLinks: "This project has no assets of this type yet.", linkFailed: "Available assets could not be loaded.", retry: "Retry",
};
const vi: typeof zh = {
  name: "Tên tài sản", description: "Mô tả", appearance: "Ngoại hình", personality: "Tính cách", speakingStyle: "Cách nói",
  environment: "Bối cảnh", lighting: "Ánh sáng", timeOfDay: "Thời điểm", purpose: "Công dụng đạo cụ", material: "Chất liệu đạo cụ", fabric: "Chất liệu vải",
  continuity: "Chi tiết cần giữ giữa các cảnh quay", setting: "Kiểu trang phục", timbre: "Âm sắc", language: "Ngôn ngữ và giọng vùng miền", emotion: "Dải cảm xúc",
  title: "Nhờ AI viết nháp", intro: "AI có thể tham khảo kịch bản đã liên kết. Hãy xem gợi ý rồi chọn nội dung đưa vào hồ sơ.",
  generate: "Tạo gợi ý", again: "Tạo lại gợi ý", working: "Đang soạn gợi ý…", direction: "Thêm ý tưởng sáng tạo (tùy chọn)",
  directionHint: "Ví dụ, giữ nét đặc trưng của thời kỳ và dùng ánh sáng dịu hơn",
  review: "Chọn gợi ý để điền", fromScript: "Đã tham khảo đoạn kịch bản gắn với tài sản này.", fromAsset: "Dựa trên tên tài sản, thông tin hiện có và ý tưởng của bạn.",
  reviewHint: "Các ô trống đã được chọn. Chọn ô đang có nội dung sẽ thay phần chữ trong ô đó.",
  current: "Nội dung hiện tại", empty: "Chưa điền", apply: "Điền {{count}} gợi ý", dismiss: "Đóng gợi ý", same: "Gợi ý trùng với thông tin hiện có. Bạn có thể tiếp tục chỉnh sửa.",
  applied: "Đã điền {{count}} gợi ý. Bạn có thể chỉnh sửa rồi chọn Lưu hồ sơ.",
  form: "Thông tin cơ bản", saving: "Đang lưu…", save: "Lưu hồ sơ", unsaved: "Có thay đổi chưa lưu",
  fail: "Chưa tạo được gợi ý. Hãy thử lại hoặc tiếp tục chỉnh sửa bên dưới.", progress: "Yêu cầu trước đang được xử lý. Hãy thử lại sau để nhận kết quả.",
  denied: "Tài khoản của bạn không thể tạo gợi ý cho tài sản này.", stale: "Đã có phiên bản mới. Hãy xử lý các chỉnh sửa và mở lại tài sản trước khi tạo gợi ý.",
  invalid: "Nội dung quá dài. Hãy rút gọn ý tưởng hoặc thông tin tài sản rồi thử lại.", voiceLink: "Giọng nói liên kết (tùy chọn)", characterLink: "Nhân vật liên kết (tùy chọn)",
  linksHint: "Chọn từ các tài sản hiện có của dự án.", choose: "Chọn tài sản", none: "Không liên kết", existing: "Tài sản đã liên kết", remove: "Bỏ liên kết",
  more: "Tải thêm", loading: "Đang tải tài sản…", noLinks: "Dự án chưa có tài sản thuộc loại này.", linkFailed: "Chưa tải được danh sách tài sản.", retry: "Thử lại",
};
for (const [language, resource] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "assetProfile", resource, true, false);
export function useAssetProfileTranslation() { return useTranslation("assetProfile"); }
