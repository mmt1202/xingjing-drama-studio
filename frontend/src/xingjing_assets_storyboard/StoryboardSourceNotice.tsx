import i18n from "@/i18n";
import { useTranslation } from "react-i18next";
import type { ShotItem } from "./contracts";

const zh = {
  title: "当前分镜采用的制作来源",
  help: "智能分镜保留创建时采用的剧本、资产快照和导演确认记录。之后保存新导演候选，不会自动替换这些来源。",
  group: "分镜组 {{number}}",
  pinned: "已记录导演确认来源",
  legacy: "此分镜未记录导演确认来源；不能据此认定已经确认。",
  details: "查看采用版本",
  script: "剧本版本",
  assets: "资产快照",
  confirmation: "导演确认记录",
  candidate: "导演候选",
};
type Copy = Record<keyof typeof zh, string>;
const en: Copy = {
  title: "Production sources used by these storyboards",
  help: "Smart storyboards retain the script, asset snapshot and director confirmation used at creation. Saving another director candidate does not replace these sources.",
  group: "Storyboard group {{number}}", pinned: "Director confirmation source recorded",
  legacy: "No director confirmation source is recorded for this storyboard. Confirmation cannot be inferred.",
  details: "View adopted versions", script: "Script version", assets: "Asset snapshot",
  confirmation: "Director confirmation", candidate: "Director candidate",
};
const vi: Copy = {
  title: "Nguồn sản xuất được bảng phân cảnh sử dụng",
  help: "Phân cảnh thông minh giữ phiên bản kịch bản, ảnh chụp tài sản và xác nhận đạo diễn tại lúc tạo. Lưu ứng viên đạo diễn mới không tự thay các nguồn này.",
  group: "Nhóm phân cảnh {{number}}", pinned: "Đã ghi nguồn xác nhận đạo diễn",
  legacy: "Phân cảnh này chưa ghi nguồn xác nhận đạo diễn; không thể suy ra là đã xác nhận.",
  details: "Xem phiên bản được chọn", script: "Phiên bản kịch bản", assets: "Ảnh chụp tài sản",
  confirmation: "Xác nhận đạo diễn", candidate: "Ứng viên đạo diễn",
};
for (const [language, copy] of Object.entries({ zh, en, vi })) {
  i18n.addResourceBundle(language, "storyboardSources", copy, true, false);
}

export function StoryboardSourceNotice({ items }: { items: readonly ShotItem[] }) {
  const { t } = useTranslation("storyboardSources");
  const groups = new Map<string, ShotItem["sourceContext"]>();
  for (const item of items) {
    if (!groups.has(item.storyboardId)) groups.set(item.storyboardId, item.sourceContext);
  }
  if (!groups.size) return null;
  return <section className="mb-5 rounded-xl border border-white/10 p-4">
    <h2 className="text-sm font-medium">{t("title")}</h2>
    <p className="mt-2 text-xs leading-6 text-slate-400">{t("help")}</p>
    <div className="mt-3 space-y-3">{[...groups].map(([id, source], index) => <div key={id} className="rounded-lg border border-white/10 p-3">
      <p className="text-xs text-slate-400">{t("group", { number: index + 1 })}</p>
      <p className={"mt-2 text-sm " + (source ? "text-violet-200" : "text-amber-200")}>{t(source ? "pinned" : "legacy")}</p>
      {source ? <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer">{t("details")}</summary><dl className="mt-3 grid gap-2 sm:grid-cols-[auto_1fr]">
        <dt>{t("script")}</dt><dd className="break-all">{source.scriptVersionId}</dd>
        <dt>{t("assets")}</dt><dd className="break-all">{source.assetSnapshotId}</dd>
        <dt>{t("confirmation")}</dt><dd className="break-all">{source.directorConfirmationId}</dd>
        <dt>{t("candidate")}</dt><dd className="break-all">{source.directorCandidateId}</dd>
      </dl></details> : null}
    </div>)}</div>
  </section>;
}
