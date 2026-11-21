import { ArrowDown, ArrowUp, Camera, Clock3, Pencil } from "lucide-react";
import type { ShotItem } from "./contracts";
import { useShotReviewTranslation } from "./shot-review-i18n";
import { button, panel } from "./ui";

export function assetDisplayName(ref: ShotItem["assetReferences"][number]) {
  const value = ref.name?.trim();
  return value && value !== ref.id && value !== ref.versionId && !/^(?:[a-z]+-)?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) ? value : undefined;
}

export function ShotAssets({ shot }: { shot: ShotItem }) {
  const { t } = useShotReviewTranslation();
  return <div className="flex flex-wrap items-center gap-1.5 text-xs leading-5">
    <span className="mr-1 text-slate-400">{t("references")}</span>
    {shot.assetReferences.length ? shot.assetReferences.map(ref => <span key={ref.id + ":" + ref.versionId} className="rounded-md bg-white/[.055] px-2 text-slate-300">
      {assetDisplayName(ref) ?? t("unknownRef", { kind: t(ref.type || "asset") })}
    </span>) : <span className="text-slate-400">{t("noAssets")}</span>}
  </div>;
}

export function ShotReviewList({ shots, order, selected, canManage, busy, onSelect, onEdit, onMove }: {
  shots: readonly ShotItem[]; order: readonly string[]; selected: readonly string[]; canManage: boolean; busy: boolean;
  onSelect?: (id: string) => void; onEdit?: (shot: ShotItem) => void; onMove?: (id: string, direction: -1 | 1) => void;
}) {
  const { t, i18n } = useShotReviewTranslation();
  const seconds = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 3 });
  return <section aria-label={t("list")} className="min-w-0 space-y-2.5">
    {shots.map(shot => {
      const index = order.indexOf(shot.id);
      const frozen = shot.status === "frozen";
      const camera = [shot.shotSize, shot.cameraMove].filter(Boolean).join(" · ");
      return <article key={shot.id} aria-label={t("shot", { no: shot.shotNo })} className={panel + " p-4 sm:px-5"}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            {onSelect && <input type="checkbox" aria-label={t("pick", { no: shot.shotNo })} checked={selected.includes(shot.id)}
              disabled={!canManage || busy || frozen} onChange={() => onSelect(shot.id)} className="size-4 accent-violet-400" />}
            <h2 className="flex items-center gap-2 text-base font-semibold text-slate-50"><Camera size={16} aria-hidden="true" className="text-violet-300"/>{t("shot", { no: shot.shotNo })}</h2>
            <span className="inline-flex items-center gap-1.5 text-sm tabular-nums text-slate-300"><Clock3 size={13} aria-hidden="true"/>{t("sec", { n: seconds.format(shot.durationMs / 1000) })}</span>
            <span className="text-sm text-slate-300">{camera || t("noCamera")}</span>
            {shot.issueCount > 0 && <span className="rounded-md bg-amber-400/10 px-2 py-0.5 text-xs text-amber-200">{t("issues", { n: shot.issueCount })}</span>}
            {shot.estimatedCredit !== undefined && <span className="text-xs text-slate-400">{t("credit", { n: shot.estimatedCredit })}</span>}
          </div>
          <div className="flex items-center gap-1.5">
            {onMove && canManage && !frozen && ([-1, 1] as const).map(direction => <button key={direction} type="button"
              className="grid size-9 place-items-center rounded-lg text-slate-400 hover:bg-white/5 hover:text-white focus-visible:outline-2 focus-visible:outline-violet-300 disabled:opacity-30"
              aria-label={t(direction < 0 ? "up" : "down", { no: shot.shotNo })} title={t(direction < 0 ? "up" : "down", { no: shot.shotNo })}
              disabled={busy || index < 0 || index + direction < 0 || index + direction >= order.length} onClick={() => onMove(shot.id, direction)}>
              {direction < 0 ? <ArrowUp size={15}/> : <ArrowDown size={15}/>}</button>)}
            {onEdit && <button type="button" className={button + " inline-flex min-h-9 items-center gap-2"} disabled={busy} onClick={() => onEdit(shot)}
              aria-label={canManage && !frozen ? t("editNamed", { no: shot.shotNo }) : t("review")}>
              <Pencil size={14} aria-hidden="true"/>{t(canManage && !frozen ? "edit" : "review")}</button>}
          </div>
        </div>
        <div className="my-2.5 space-y-1 text-sm leading-6">
          {shot.prompt && <p className="line-clamp-2 break-words text-slate-200"><span className="mr-2 font-medium text-slate-400">{t("prompt")}</span>{shot.prompt}</p>}
          {shot.dialogue && <p className="line-clamp-2 break-words text-slate-300">{shot.dialogue}</p>}
          {!shot.prompt && !shot.dialogue && <p className="text-slate-400">{t("noText")}</p>}
        </div>
        <ShotAssets shot={shot}/>
        <details className="mt-2 text-[11px] text-slate-400"><summary className="w-fit cursor-pointer">{t("technical")}</summary>
          <dl className="mt-1 grid gap-1"><div><dt className="inline">{t("technicalShot")} </dt><dd className="inline break-all font-mono">{shot.id}</dd></div>
          <div><dt className="inline">{t("technicalBoard")} </dt><dd className="inline break-all font-mono">{shot.storyboardId}</dd></div></dl>
        </details>
      </article>;
    })}
  </section>;
}
