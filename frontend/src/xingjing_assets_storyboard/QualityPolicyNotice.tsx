import type { QualityPolicyDescriptor } from "./contracts";
import { useStoryboardQualityTranslation } from "./quality-i18n";
import { panel } from "./ui";

export function QualityPolicyNotice({
  policy,
}: {
  readonly policy?: QualityPolicyDescriptor;
}) {
  const { t } = useStoryboardQualityTranslation();
  const rules = policy?.configured ? policy.rules : undefined;
  return (
    <aside className={`${panel} mb-4 space-y-2 p-5 text-sm text-[#B8BECC]`}>
      <h2 className="font-semibold text-[#F0F2F8]">{t("title")}</h2>
      <p>{t("scope")}</p>
      <p>{t("blockers")}</p>
      {rules ? (
        <>
          <p>
            {t("thresholds", {
              minimum: rules.minimum_duration_ms / 1000,
              maximum: rules.maximum_duration_ms / 1000,
              delta: rules.max_adjacent_duration_delta_ms / 1000,
              characters: rules.max_dialogue_characters_per_second,
            })}
          </p>
          <p>
            {rules.compliance_terms.length
              ? t("keywords", { count: rules.compliance_terms.length })
              : t("noKeywords")}
          </p>
          <details>
            <summary className="cursor-pointer">{t("evidence")}</summary>
            <code className="mt-2 block break-all text-xs">
              {policy?.policyId} · v{policy?.version} · {policy?.digest}
            </code>
          </details>
        </>
      ) : (
        <p>{t("unavailable")}</p>
      )}
    </aside>
  );
}
