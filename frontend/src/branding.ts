// Brand configuration — single source of truth for product naming.
// Override at build time via Vite env vars
// (VITE_BRAND_NAME / VITE_BRAND_TAGLINE / VITE_BRAND_DESCRIPTION).
//
// Source code references BRAND.name (or the [[brand]] placeholder in i18n
// resources) so the displayed product name is not hardcoded across files.
// Defaults identify this Xingjing distribution; frontend/.env can override them.

const env = import.meta.env as Record<string, string | undefined>;

function fallback(value: string | undefined, defaultValue: string): string {
  // Trim + empty check so VITE_BRAND_NAME="" (or whitespace) falls back to the
  // configured product default.
  if (typeof value !== "string") return defaultValue;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : defaultValue;
}

export const BRAND = {
  name: fallback(env.VITE_BRAND_NAME, "星镜剧创"),
  tagline: fallback(env.VITE_BRAND_TAGLINE, "AI 短剧与漫剧生产工作台"),
  description: fallback(
    env.VITE_BRAND_DESCRIPTION,
    "星镜剧创，面向 AI 短剧与漫剧团队，连接剧本、资产、分镜、生成、剪辑与协作交付。",
  ),
} as const;

export const BRAND_DOCUMENT_TITLE = `${BRAND.name} · ${BRAND.tagline}`;

