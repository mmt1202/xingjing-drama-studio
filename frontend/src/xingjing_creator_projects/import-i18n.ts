import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  title: "小说与剧本", create: "导入新素材", existing: "继续已有剧本", file: "上传文件", paste: "粘贴正文",
  intro: "选择小说或剧本，导入后会自动识别章节和场景。", pick: "选择文件或拖到这里", replace: "更换文件",
  formats: "TXT、Markdown 或 DOCX，最大 20 MB", large: "文件超过 20 MB，请按章节拆成较小的文件后导入。",
  emptyFile: "这个文件是空的，请选择包含正文的文件。", oneFile: "一次导入一份文档，请选择一个文件。",
  body: "小说或剧本正文", bodyHint: "把章节原文或分场剧本粘贴到这里…", options: "标题与 AI 选项",
  titleLabel: "文档标题", fallbackTitle: "未命名素材", defaultTitle: "默认取文件名或正文首行。",
  checking: "正在检查文本模型…", aiRetry: "重新检查模型", aiHelp: "仅在勾选后调用模型，可能产生费用。",
  saved: "已保存在项目中", switch: "选择其他文档", openText: "查看正文",
  existingHint: "找到要继续制作的小说或剧本。", search: "按文档标题搜索", noMatches: "没有找到匹配的文档。", previous: "上一页", next: "下一页",
  page: "第 {{page}} / {{pages}} 页 · 共 {{count}} 份", total: "项目中已有 {{count}} 份文档",
  continueEpisode: "继续改编本集", adapt: "将原文改编为剧本", split: "按原文规划分集", trim: "选取本集改编片段",
  structure: "核对分场结构", revise: "修订工作稿", freeze: "核对并冻结剧本", frozen: "查看冻结版本",
  episodeHelp: "这份工作稿已归入第 {{episode}} 集，可以直接继续制作。", proseHelp: "原文已保存，可以开始改编人物对白和场景。",
  splitHelp: "先确认各集的原文范围，再分别改编工作稿。", trimHelp: "本集正文超过单次改编上限，请先选取要改编的片段。",
  screenplayHelp: "已识别到分场结构，可以核对场景和对白。", freezeHelp: "已识别到分场结构，请核对内容并决定是否冻结。", frozenHelp: "这份剧本已冻结，可查看保存的版本。",
  pasted: "粘贴的正文", metadata: "文档信息", chars: "{{count}} 字符",
  noSelection: "没有读到这份文档，请重新选择。", change: "更换素材", newHint: "导入将创建一份新文档。",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  title: "Novels and scripts", create: "Import new material", existing: "Continue an existing script", file: "Upload a file", paste: "Paste text",
  intro: "Import a novel or screenplay to identify its chapters and scenes.", pick: "Choose a file or drop it here", replace: "Change file",
  formats: "TXT, Markdown or DOCX, up to 20 MB", large: "The file exceeds 20 MB. Split it into smaller chapter files.", emptyFile: "This file is empty. Choose one with text.", oneFile: "Choose one file per import.",
  body: "Novel or screenplay text", bodyHint: "Paste chapter text or a screenplay here…", options: "Title and AI options",
  titleLabel: "Document title", fallbackTitle: "Untitled material", defaultTitle: "Defaults to the filename or first line of text.",
  checking: "Checking text model…", aiRetry: "Check model again", aiHelp: "Enabling this option calls the model and may incur a charge.",
  saved: "Saved in this project", switch: "Choose another document", openText: "View text",
  existingHint: "Find the novel or script you want to work on.", search: "Search by document title", noMatches: "No matching documents.", previous: "Previous page", next: "Next page",
  page: "Page {{page}} of {{pages}} · {{count}} documents", total: "{{count}} documents in this project",
  continueEpisode: "Continue adapting this episode", adapt: "Adapt into a screenplay", split: "Plan episodes", trim: "Choose an episode excerpt",
  structure: "Review scene structure", revise: "Edit working script", freeze: "Review and freeze script", frozen: "View frozen version",
  episodeHelp: "This working script belongs to episode {{episode}} and is ready to continue.", proseHelp: "The source is saved. You can now adapt its dialogue and scenes.",
  splitHelp: "Confirm the source range for each episode, then adapt its working script.", trimHelp: "This episode exceeds the single adaptation limit. Select an excerpt first.",
  screenplayHelp: "Scene structure was recognized. Review the scenes and dialogue.", freezeHelp: "Scene structure was recognized. Review the text before freezing it.", frozenHelp: "This script is frozen. You can review the saved version.",
  pasted: "Pasted text", metadata: "Document information", chars: "{{count}} characters",
  noSelection: "This document could not be loaded. Choose it again.", change: "Change source", newHint: "Importing creates a new document.",
};
const vi: Copy = {
  title: "Tiểu thuyết và kịch bản", create: "Nhập tư liệu mới", existing: "Tiếp tục kịch bản đã có", file: "Tải tệp lên", paste: "Dán văn bản",
  intro: "Nhập tiểu thuyết hoặc kịch bản để tự động nhận diện chương và cảnh.", pick: "Chọn tệp hoặc kéo thả vào đây", replace: "Đổi tệp",
  formats: "TXT, Markdown hoặc DOCX, tối đa 20 MB", large: "Tệp vượt quá 20 MB. Hãy chia thành các tệp nhỏ theo chương.", emptyFile: "Tệp trống. Hãy chọn tệp có nội dung.", oneFile: "Mỗi lần nhập một tài liệu. Hãy chọn một tệp.",
  body: "Nội dung tiểu thuyết hoặc kịch bản", bodyHint: "Dán nội dung chương hoặc kịch bản phân cảnh vào đây…", options: "Tiêu đề và tùy chọn AI",
  titleLabel: "Tiêu đề tài liệu", fallbackTitle: "Tư liệu chưa có tiêu đề", defaultTitle: "Mặc định dùng tên tệp hoặc dòng văn bản đầu tiên.",
  checking: "Đang kiểm tra mô hình văn bản…", aiRetry: "Kiểm tra lại mô hình", aiHelp: "Bật tùy chọn này sẽ gọi mô hình và có thể phát sinh phí.",
  saved: "Đã lưu trong dự án", switch: "Chọn tài liệu khác", openText: "Xem văn bản",
  existingHint: "Tìm tiểu thuyết hoặc kịch bản để tiếp tục.", search: "Tìm theo tiêu đề", noMatches: "Không tìm thấy tài liệu phù hợp.", previous: "Trang trước", next: "Trang sau",
  page: "Trang {{page}} / {{pages}} · {{count}} tài liệu", total: "Dự án có {{count}} tài liệu",
  continueEpisode: "Tiếp tục chuyển thể tập này", adapt: "Chuyển thể thành kịch bản", split: "Lập kế hoạch chia tập", trim: "Chọn trích đoạn của tập",
  structure: "Kiểm tra cấu trúc cảnh", revise: "Sửa bản làm việc", freeze: "Đối chiếu và đóng băng kịch bản", frozen: "Xem bản đã đóng băng",
  episodeHelp: "Bản làm việc này thuộc tập {{episode}}. Bạn có thể tiếp tục sản xuất.", proseHelp: "Đã lưu nguyên tác. Bạn có thể chuyển thể lời thoại và bối cảnh.",
  splitHelp: "Xác nhận phần nguyên tác của từng tập trước khi chuyển thể.", trimHelp: "Tập này vượt giới hạn mỗi lần chuyển thể. Hãy chọn một trích đoạn trước.",
  screenplayHelp: "Đã nhận diện phân cảnh. Hãy kiểm tra cảnh và lời thoại.", freezeHelp: "Đã nhận diện phân cảnh. Kiểm tra nội dung trước khi đóng băng.", frozenHelp: "Kịch bản đã đóng băng. Bạn có thể xem phiên bản đã lưu.",
  pasted: "Văn bản đã dán", metadata: "Thông tin tài liệu", chars: "{{count}} ký tự",
  noSelection: "Không thể đọc tài liệu này. Hãy chọn lại.", change: "Đổi tư liệu", newHint: "Mỗi lần nhập sẽ tạo tài liệu mới.",
};
for (const [language, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "scriptImport", copy, true, false);
export const useImportTranslation = () => useTranslation("scriptImport");
