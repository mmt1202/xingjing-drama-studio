import i18n from "@/i18n";
import { useTranslation } from "react-i18next";

const zh = {
  hint: "集中管理你的项目，继续配置或进入制作。",
  loading: "正在加载项目…",
  empty: "这个工作区还没有项目",
  emptyHint: "创建项目后可继续完善配置、导入剧本并进入生产。",
  open: "进入总控",
  archive: "归档",
  restore: "恢复",
  archiveProject: "归档{{name}}",
  restoreProject: "恢复{{name}}",
};
type Copy = { [K in keyof typeof zh]: string };
const en: Copy = {
  hint: "Manage your projects, continue setup or return to production.",
  loading: "Loading projects…",
  empty: "This workspace has no projects yet",
  emptyHint: "Create a project to configure production, import a script and get started.",
  open: "Open overview",
  archive: "Archive",
  restore: "Restore",
  archiveProject: "Archive {{name}}",
  restoreProject: "Restore {{name}}",
};
const vi: Copy = {
  hint: "Quản lý dự án, tiếp tục cấu hình hoặc quay lại sản xuất.",
  loading: "Đang tải dự án…",
  empty: "Không gian này chưa có dự án",
  emptyHint: "Tạo dự án để cấu hình sản xuất, nhập kịch bản và bắt đầu.",
  open: "Mở tổng quan",
  archive: "Lưu trữ",
  restore: "Khôi phục",
  archiveProject: "Lưu trữ {{name}}",
  restoreProject: "Khôi phục {{name}}",
};
for (const [language, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(language, "projectLibrary", copy, true, false);
export const useLibraryTranslation = () => useTranslation("projectLibrary");
