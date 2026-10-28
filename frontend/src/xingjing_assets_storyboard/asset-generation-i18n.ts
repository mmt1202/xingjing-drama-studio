import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  recoveryStorageUnavailable: "浏览器无法保存操作恢复信息，尚未发送新操作。请允许当前站点使用会话存储后重试。",
  receiptStorageUnavailable: "服务端已返回任务，但浏览器暂时无法保存回执。原提交已保留，请恢复原提交，不要新建重复任务。",
  detailFailed: "资产详情读取失败", reloadDetail: "重新读取这项资产",
  ownerProjectRequired: "这是其他项目共享的资产。可使用已授权的媒体版本；生成参考图或采用新版本，请进入资产所属项目管理。",
  recoverSubmission: "恢复原提交", uncertain: "尚未确认服务端是否已接收本次操作。请恢复原提交以读取原任务结果；当前参数和版本已保留，避免重复生成。",
  title: "资产参考图", intro: "根据已保存的资产设定生成参考图；生成完成后，预览并采用为资产新版本。",
  generate: "生成参考图", regenerate: "生成新的参考图候选", submitting: "正在提交…", savedFirst: "请先保存当前资产修改，再生成或采用参考图。",
  noImage: "当前主版本只有文字设定。先生成并采用参考图，再制作三视图或表情集。",
  noDescription: "请先保存资产说明或外观等设定，帮助模型生成符合角色、场景或道具的图片。",
  voice: "声音资产使用音频制作流程，不生成图片参考。", sourceVersion: "使用主版本 v{{number}} 的已保存设定",
  preview: "{{name}} 的参考图", candidate: "{{name}} 的候选参考图 {{number}}", candidateTitle: "待采用的参考图候选", candidateHint: "生成完成不会自动替换主版本。采用后，后续制作将使用新资产版本。",
  taskTitle: "参考图生成任务", adopt: "采用为参考图新版本", adopted: "已采用此候选", waiting: "任务处理中，请等待候选图片完成后采用。",
  firstImage: "先生成并采用参考图", sourceMissing: "当前没有可用主版本，请先保存资产。", failure: "生成未完成，可检查模型配置和任务错误后再次提交。",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  recoveryStorageUnavailable: "The browser could not save command recovery information. No new command was sent. Enable session storage for this site and retry.",
  receiptStorageUnavailable: "The server returned a task, but the browser could not save its receipt. The original command is retained. Recover it instead of creating a duplicate task.",
  detailFailed: "Asset details could not be read", reloadDetail: "Reload this asset",
  ownerProjectRequired: "This asset is shared from another project. Authorized media versions may be used here. Generate reference images or adopt new versions in the asset's owning project.",
  recoverSubmission: "Recover original submission", uncertain: "The server may have accepted this operation. Recover the original submission to retrieve its result. The original input and version are retained to avoid duplicate generation.",
  title: "Asset reference image", intro: "Generate an image from the saved asset description, then preview and adopt it as a new asset version.",
  generate: "Generate reference image", regenerate: "Generate a new reference candidate", submitting: "Submitting…", savedFirst: "Save your asset changes before generating or adopting a reference image.",
  noImage: "The primary version only has text. Generate and adopt a reference image before creating turnarounds or expressions.",
  noDescription: "Save a description or appearance details to guide an image that matches this character, scene or prop.",
  voice: "Voice assets use the audio workflow and do not generate image references.", sourceVersion: "Uses the saved settings from primary version v{{number}}",
  preview: "Reference image for {{name}}", candidate: "Reference candidate {{number}} for {{name}}", candidateTitle: "Reference candidates awaiting adoption", candidateHint: "Generation does not replace the primary version. Adopt a candidate to use its new asset version in production.",
  taskTitle: "Reference image generation", adopt: "Adopt as new reference version", adopted: "Candidate adopted", waiting: "The task is processing. Wait for the image candidates before adopting.",
  firstImage: "Generate and adopt a reference first", sourceMissing: "No primary version is available. Save the asset first.", failure: "Generation did not complete. Check the model configuration and task error before submitting again.",
};
const vi: Copy = {
  recoveryStorageUnavailable: "Trình duyệt không lưu được thông tin khôi phục; chưa gửi thao tác mới. Hãy cho phép lưu trữ phiên cho trang này rồi thử lại.",
  receiptStorageUnavailable: "Máy chủ đã trả về tác vụ nhưng trình duyệt chưa lưu được biên nhận. Lệnh gốc được giữ; hãy khôi phục lệnh đó thay vì tạo tác vụ trùng.",
  detailFailed: "Chưa đọc được chi tiết tài sản", reloadDetail: "Tải lại tài sản này",
  ownerProjectRequired: "Tài sản này được chia sẻ từ dự án khác. Có thể dùng phiên bản phương tiện đã cấp quyền. Hãy tạo ảnh tham chiếu hoặc áp dụng phiên bản mới trong dự án sở hữu tài sản.",
  recoverSubmission: "Khôi phục lần gửi ban đầu", uncertain: "Máy chủ có thể đã nhận thao tác. Khôi phục lần gửi ban đầu để lấy kết quả. Đầu vào và phiên bản gốc được giữ để tránh tạo trùng.",
  title: "Ảnh tham chiếu tài sản", intro: "Tạo ảnh từ mô tả tài sản đã lưu, sau đó xem và áp dụng làm phiên bản tài sản mới.",
  generate: "Tạo ảnh tham chiếu", regenerate: "Tạo ứng viên tham chiếu mới", submitting: "Đang gửi…", savedFirst: "Lưu thay đổi tài sản trước khi tạo hoặc áp dụng ảnh tham chiếu.",
  noImage: "Phiên bản chính chỉ có văn bản. Tạo và áp dụng ảnh tham chiếu trước khi tạo ba góc nhìn hoặc biểu cảm.",
  noDescription: "Lưu mô tả hoặc đặc điểm ngoại hình để mô hình tạo ảnh đúng với nhân vật, bối cảnh hoặc đạo cụ.",
  voice: "Tài sản giọng nói sử dụng quy trình âm thanh, không tạo ảnh tham chiếu.", sourceVersion: "Sử dụng thiết lập đã lưu từ phiên bản chính v{{number}}",
  preview: "Ảnh tham chiếu của {{name}}", candidate: "Ứng viên tham chiếu {{number}} của {{name}}", candidateTitle: "Ảnh tham chiếu chờ áp dụng", candidateHint: "Tạo ảnh không tự thay phiên bản chính. Áp dụng ứng viên để dùng phiên bản tài sản mới trong sản xuất.",
  taskTitle: "Tác vụ tạo ảnh tham chiếu", adopt: "Áp dụng làm phiên bản tham chiếu mới", adopted: "Đã áp dụng ứng viên", waiting: "Tác vụ đang xử lý. Đợi ảnh hoàn tất trước khi áp dụng.",
  firstImage: "Tạo và áp dụng ảnh tham chiếu trước", sourceMissing: "Chưa có phiên bản chính. Hãy lưu tài sản trước.", failure: "Chưa tạo ảnh thành công. Kiểm tra cấu hình mô hình và lỗi tác vụ trước khi gửi lại.",
};
for (const [language, resource] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "assetGeneration", resource, true, false);
export function useAssetGenerationTranslation() { return useTranslation("assetGeneration"); }
