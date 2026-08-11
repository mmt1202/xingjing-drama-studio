import type { ReactNode } from "react";

export function StatusPanel({ title, message, action, tone = "neutral" }: {
  title: string; message: string; action?: ReactNode; tone?: "neutral" | "danger" | "warning";
}) {
  const border = tone === "danger" ? "border-red-400/30" : tone === "warning" ? "border-amber-400/30" : "border-white/10";
  return (
    <section className={`rounded-2xl border ${border} bg-white/[0.04] p-6 text-center`} aria-live="polite">
      <h2 className="text-lg font-semibold text-white">{title}</h2>
      <p className="mt-2 text-sm text-white/60">{message}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </section>
  );
}
