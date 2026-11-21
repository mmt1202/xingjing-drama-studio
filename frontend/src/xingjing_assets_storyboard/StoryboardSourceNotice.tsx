import type { ShotItem } from "./contracts";
import { useBoardText } from "./board-studio-i18n";
import { useShotReviewTranslation } from "./shot-review-i18n";

export function StoryboardSourceNotice({ items }: { items: readonly ShotItem[] }) {
  const { t } = useBoardText();
  const { t: review } = useShotReviewTranslation();
  const groups = new Map<string, ShotItem["sourceContext"]>();
  for (const shot of items)
    if (!groups.has(shot.storyboardId)) groups.set(shot.storyboardId, shot.sourceContext);
  return (
    <div className="grid gap-3 text-sm">
      {[...groups].map(([id, source], index) => {
        const version = source?.scriptVersionId.match(/@(\d+)$/)?.[1];
        return (
          <section key={id} className="my-2 rounded-lg border border-white/10 p-3 text-slate-300">
            {groups.size > 1 && <h3 className="mb-2 font-semibold">{review("group", { n: index + 1 })}</h3>}
            {source ? (
              <dl className="grid gap-x-5 gap-y-1.5 sm:grid-cols-[auto_1fr]">
                <dt className="text-slate-400">{t("sourceScript")}</dt>
                <dd>{version ? t("scriptVersion", { n: version }) : t("scriptPinned")}</dd>
                <dt className="text-slate-400">{t("sourceAssets")}</dt>
                <dd>{t("assetPinned")}</dd>
                <dt className="text-slate-400">{t("sourceDirector")}</dt>
                <dd>{t("directorPinned")}</dd>
              </dl>
            ) : (
              <p>{t("historical")}</p>
            )}
          </section>
        );
      })}
    </div>
  );
}
