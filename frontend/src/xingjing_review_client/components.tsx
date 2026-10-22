import { Clock3, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ReviewApiError, type ReviewComment } from "./api";
import { useReviewTranslation } from "./i18n";

export const inputClass = "w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none focus:border-amber-300";
export const buttonClass = "rounded-xl border border-white/20 px-4 py-2.5 text-sm text-amber-100 disabled:opacity-50";
export const primaryClass = "rounded-xl bg-amber-300 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:opacity-50";

export function ReviewShell({ eyebrow, title, meta, children }: { eyebrow: string; title: string; meta?: string; children: ReactNode }) {
  return <main className="min-h-screen bg-[#0b1020] px-5 py-10 text-slate-100 sm:px-10">
    <div className="mx-auto max-w-7xl">
      <header className="mb-8 border-b border-white/10 pb-6"><p className="text-xs font-semibold tracking-[.2em] text-amber-200">{eyebrow}</p><h1 className="mt-3 font-serif text-3xl text-white sm:text-4xl">{title}</h1>{meta && <p className="mt-3 break-all font-mono text-xs text-slate-400">{meta}</p>}</header>
      {children}
    </div>
  </main>;
}

export function LoadingMark() {
  const { t } = useReviewTranslation();
  return <p role="status" className="flex items-center gap-2 py-6 text-sm text-slate-400"><Clock3 size={16} className="animate-pulse" />{t("loading")}</p>;
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useReviewTranslation();
  const code = error instanceof ReviewApiError
    ? error.code === "REVIEW_SCREENSHOT_INVALID" ? "fileError"
      : error.code === "REVIEW_MEDIA_NOT_AVAILABLE" ? "mediaError"
      : error.status === 409 ? "conflictError"
        : [401, 403, 404].includes(error.status) ? "accessError"
          : error.status === 503 ? "serviceError"
            : error.status === 0 ? "networkError" : "actionError"
    : "networkError";
  return <div role="alert" className="mt-5 flex items-center justify-between gap-4 rounded-2xl border border-rose-400/30 bg-rose-950/40 px-4 py-3 text-sm text-rose-100">
    <div><p>{t(code)}</p>{error instanceof ReviewApiError && error.requestId && <p className="mt-1 text-xs">{t("requestId", { id: error.requestId })}</p>}</div>
    {onRetry && <button type="button" onClick={onRetry} className={buttonClass}><RefreshCw size={14} className="mr-2 inline" />{t("retry")}</button>}
  </div>;
}

export function ReviewTime({ value }: { value: string | null }) {
  const { t, i18n } = useReviewTranslation();
  const date = value ? new Date(value) : null;
  return <>{!date ? t("noExpiry") : Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(date)}</>;
}

export function ReviewStatus({ value }: { value: string }) {
  const { t } = useReviewTranslation();
  return <>{t("status_" + value, { defaultValue: value })}</>;
}

export function toTimecode(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}.${String(milliseconds % 1000).padStart(3, "0")}`;
}

export function orderedComments(comments: ReviewComment[]): { comment: ReviewComment; depth: number }[] {
  const ids = new Set(comments.map((comment) => comment.id));
  const children = new Map<string | null, ReviewComment[]>();
  for (const comment of comments) {
    const parent = comment.parentCommentId && ids.has(comment.parentCommentId) ? comment.parentCommentId : null;
    children.set(parent, [...children.get(parent) ?? [], comment]);
  }
  const result: { comment: ReviewComment; depth: number }[] = [];
  const visited = new Set<string>();
  const append = (comment: ReviewComment, depth: number) => {
    if (visited.has(comment.id)) return;
    visited.add(comment.id);
    result.push({ comment, depth });
    for (const child of children.get(comment.id) ?? []) append(child, depth + 1);
  };
  for (const comment of children.get(null) ?? []) append(comment, 0);
  for (const comment of comments) append(comment, 0);
  return result;
}
