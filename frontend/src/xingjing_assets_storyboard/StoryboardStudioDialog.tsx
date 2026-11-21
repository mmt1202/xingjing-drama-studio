import { useId } from "react";
import { useTranslation } from "react-i18next";
import { ModalShell } from "@/components/ui/ModalShell";

export function BoardDialog({
  title,
  detail,
  label,
  disabled,
  onClose,
  onConfirm,
}: {
  title: string;
  detail: string;
  label: string;
  disabled?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const titleId = useId(),
    detailId = useId();
  const { t } = useTranslation("common");
  return (
    <ModalShell
      open
      onClose={onClose}
      labelledBy={titleId}
      describedBy={detailId}
      className="w-full max-w-md rounded-2xl border border-white/15 bg-[#171a24] p-6 text-slate-100 shadow-2xl"
    >
      <h2 id={titleId} className="text-xl font-semibold">
        {title}
      </h2>
      <p id={detailId} className="mt-3 text-sm leading-7 text-slate-300">
        {detail}
      </p>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" className="xj-board-action" onClick={onClose}>
          {t("cancel")}
        </button>
        <button type="button" className="xj-board-primary" disabled={disabled} onClick={onConfirm}>
          {label}
        </button>
      </div>
    </ModalShell>
  );
}
