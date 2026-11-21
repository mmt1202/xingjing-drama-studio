import { useTranslation } from "react-i18next";
import i18n from "@/i18n";

const zh = {
  navigation: "列表分页", previous: "上一页", next: "下一页", page: "第 {{page}} 页",
  jump: "跳页", input: "跳转页码，1 至 {{pages}}", submit: "跳转", position: "第 {{page}} / {{pages}} 页，选择页码",
  items: "{{count, number}} 项", episodes: "{{count, number}} 集", chapters: "{{count, number}} 章",
  shots: "{{count, number}} 个镜头", documents: "{{count, number}} 份文档", onPage: "本页 {{count, number}} 项",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  navigation: "List pagination", previous: "Previous page", next: "Next page", page: "Page {{page}}",
  jump: "Go to page", input: "Page number, 1 to {{pages}}", submit: "Go", position: "Page {{page}} of {{pages}}, choose a page",
  items: "{{count, number}} items", episodes: "{{count, number}} episodes", chapters: "{{count, number}} chapters",
  shots: "{{count, number}} shots", documents: "{{count, number}} documents", onPage: "{{count, number}} items on this page",
};
const vi: Copy = {
  navigation: "Phân trang danh sách", previous: "Trang trước", next: "Trang sau", page: "Trang {{page}}",
  jump: "Đến trang", input: "Số trang, từ 1 đến {{pages}}", submit: "Đến", position: "Trang {{page}} trên {{pages}}, chọn trang",
  items: "{{count, number}} mục", episodes: "{{count, number}} tập", chapters: "{{count, number}} chương",
  shots: "{{count, number}} cảnh quay", documents: "{{count, number}} tài liệu", onPage: "{{count, number}} mục trên trang này",
};
for (const [locale, copy] of Object.entries({ zh, en, vi })) i18n.addResourceBundle(locale, "pagination", copy, true, false);
export const usePaginationTranslation = () => useTranslation("pagination");
