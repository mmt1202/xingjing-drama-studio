import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  adaptWithAi: "用 AI 改编为分场剧本",
  heading: "原文识别", screenplay: "分场剧本", chapters: "分章原文", prose: "叙事原文", path: "文件路径",
  counts: "{{characters}} 字 · {{paragraphs}} 段正文", scenes: "已识别 {{count}} 场", chaptersFound: "已识别 {{count}} 章",
  pathHint: "这里只收到了文件路径。请选择本机文件，或粘贴小说、剧本的正文。", selectFile: "选择原文件",
  split: "按这份原文分集", longHelp: "全文超过单次 AI 改编上限（{{limit}} 字符）。先预览并确认分集，再改编每一集的工作稿。",
  continueLab: "继续改编这份原文", review: "核对剧本并冻结", fix: "编辑这份工作稿", reparse: "将识别结果保存为新版本",
  reading: "预览原文旁白稿", readingHelp: "把每段原文作为旁白，生成可编辑的朗读稿。对白改编和场景设计可使用 AI 或由编剧完成。",
  readingHeading: "场景：原文朗读", narrationPrefix: "旁白：", readingSave: "保存旁白稿为新版本", readingSummary: "按原文段落整理旁白稿",
  formatSummary: "识别已有剧本的场景格式", cancel: "收起预览", retry: "重新读取识别结果", failed: "暂时无法读取这份原文的识别结果。",
  loading: "正在识别当前文档…", wrong: "文档已更新，请重新读取后继续。", saved: "已保存新的工作版本。",
  automatic: "导入后自动用 AI 改编短篇原文", automaticHelp: "适用于短篇原文。长篇可先分集，已有分场的剧本会直接识别。",
  autoUnavailable: "AI 改编暂时不可用。你仍可以导入原文并自动识别章节和场次。", processing: "原文已保存，AI 正在改编这份原文…",
  fileSource: "导入文件", identified: "导入时会自动识别章节、常见场次编号和标准场景标题。",
  simpleNext: "可把原文整理为旁白稿，也可以用 AI 改编为分场剧本。",
  originalRanges: "分集取材使用导入时保存的原文。当前工作版本另行保留。",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  adaptWithAi: "Adapt into a screenplay with AI",
  heading: "Source recognition", screenplay: "Screenplay", chapters: "Chaptered text", prose: "Narrative text", path: "File path",
  counts: "{{characters}} characters · {{paragraphs}} text paragraphs", scenes: "{{count}} scenes recognized", chaptersFound: "{{count}} chapters recognized",
  pathHint: "Only a file path was received. Choose a local file or paste the novel or screenplay text.", selectFile: "Choose the original file",
  split: "Plan episodes from this source", longHelp: "This text exceeds the {{limit}}-character limit for one AI adaptation. Preview and confirm episodes first, then adapt each episode’s working script.",
  continueLab: "Continue with this source", review: "Review and freeze this script", fix: "Edit this working script", reparse: "Save the recognized structure as a new version",
  reading: "Preview a narrated script", readingHelp: "Use the original paragraphs as narration in an editable script. For adapted dialogue and scene design, use AI or edit the script yourself.",
  readingHeading: "Scene: Reading the original text", narrationPrefix: "Narration: ", readingSave: "Save the narrated script as a new version", readingSummary: "Prepared narration from original paragraphs",
  formatSummary: "Recognized scene headings in the existing screenplay", cancel: "Close preview", retry: "Reload recognition", failed: "Source recognition could not be loaded.",
  loading: "Recognizing this document…", wrong: "This document changed. Reload it to continue.", saved: "A new working version was saved.",
  automatic: "Automatically adapt short prose with AI after importing", automaticHelp: "Use this for short texts. Split longer works into episodes first. Existing scene headings are recognised automatically.",
  autoUnavailable: "AI adaptation is temporarily unavailable. Importing text and recognising chapters and scenes are still available.", processing: "Source saved. AI adaptation is in progress…",
  fileSource: "Imported file", identified: "Import recognizes chapters, numbered scenes and standard scene headings.",
  simpleNext: "Prepare a narration script or use AI to adapt the text into scenes.",
  originalRanges: "Episode planning uses the original imported text. The current working version is saved separately.",
};
const vi: Copy = {
  adaptWithAi: "Dùng AI chuyển thể thành kịch bản phân cảnh",
  heading: "Nhận diện nguyên văn", screenplay: "Kịch bản phân cảnh", chapters: "Văn bản có chương", prose: "Văn xuôi", path: "Đường dẫn tệp",
  counts: "{{characters}} ký tự · {{paragraphs}} đoạn văn", scenes: "Đã nhận diện {{count}} cảnh", chaptersFound: "Đã nhận diện {{count}} chương",
  pathHint: "Chỉ nhận được đường dẫn tệp. Hãy chọn tệp trên máy hoặc dán nội dung tiểu thuyết hay kịch bản.", selectFile: "Chọn tệp gốc",
  split: "Chia tập từ nguyên văn này", longHelp: "Văn bản vượt giới hạn {{limit}} ký tự của một lần chuyển thể bằng AI. Hãy xem trước, xác nhận các tập rồi chuyển thể bản làm việc của từng tập.",
  continueLab: "Tiếp tục chuyển thể nguyên văn", review: "Kiểm tra và đóng băng kịch bản", fix: "Sửa bản làm việc này", reparse: "Lưu cấu trúc đã nhận diện thành bản mới",
  reading: "Xem trước bản lời dẫn", readingHelp: "Dùng từng đoạn nguyên văn làm lời dẫn trong bản có thể chỉnh sửa. Có thể dùng AI hoặc tự viết lời thoại và bố trí cảnh.",
  readingHeading: "Cảnh: Đọc nguyên văn", narrationPrefix: "Lời dẫn: ", readingSave: "Lưu bản lời dẫn thành phiên bản mới", readingSummary: "Sắp xếp lời dẫn theo đoạn nguyên văn",
  formatSummary: "Nhận diện tiêu đề cảnh của kịch bản hiện có", cancel: "Đóng bản xem trước", retry: "Tải lại kết quả nhận diện", failed: "Chưa tải được kết quả nhận diện nguyên văn.",
  loading: "Đang nhận diện tài liệu…", wrong: "Tài liệu đã thay đổi. Hãy tải lại để tiếp tục.", saved: "Đã lưu phiên bản làm việc mới.",
  automatic: "Tự động chuyển thể văn xuôi ngắn bằng AI sau khi nhập", automaticHelp: "Dùng cho văn bản ngắn. Chia tác phẩm dài thành tập trước. Các tiêu đề cảnh sẵn có được nhận diện tự động.",
  autoUnavailable: "Tạm thời chưa thể chuyển thể bằng AI. Bạn vẫn có thể nhập văn bản để tự nhận diện chương và cảnh.", processing: "Đã lưu nguyên văn. AI đang chuyển thể…",
  fileSource: "Tệp đã nhập", identified: "Tự động nhận diện chương, số cảnh và tiêu đề cảnh khi nhập.",
  simpleNext: "Bạn có thể soạn lời dẫn hoặc dùng AI chuyển văn bản thành kịch bản phân cảnh.",
  originalRanges: "Chia tập dùng nguyên văn khi nhập. Phiên bản làm việc hiện tại được lưu riêng.",
};
for (const [locale, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(locale, "scriptRecognition", copy, true, false);
export const useRecognitionTranslation = () => useTranslation("scriptRecognition");
