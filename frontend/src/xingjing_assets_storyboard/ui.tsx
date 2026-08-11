import type { ReactNode } from "react";

import {
  failedBatchItemIds,
  type BatchReceipt,
  type SessionContext,
  type TaskReceipt,
} from "./contracts";

const panel = "rounded-[18px] border border-[#2A2E3A] bg-[#12141B]";
const button =
  "rounded-[10px] border border-[#3A3F4E] bg-[#171922] px-3 py-2 text-sm font-medium text-[#F5F6FA] transition hover:border-[#8B5CF6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#8B5CF6] disabled:cursor-not-allowed disabled:opacity-45";
const primaryButton =
  "rounded-[10px] bg-[#FF6B4A] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#ff7b60] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#FF9A84] disabled:cursor-not-allowed disabled:opacity-45";

export { button, panel, primaryButton };

export function WorkspaceShell({
  context,
  title,
  eyebrow,
  description,
  projectName,
  children,
  actions,
}: {
  readonly context: SessionContext;
  readonly title: string;
  readonly eyebrow: string;
  readonly description: string;
  readonly projectName?: string;
  readonly children: ReactNode;
  readonly actions?: ReactNode;
}) {
  return (
    <main
      className="min-h-full bg-[#07080C] p-4 text-[#F5F6FA] sm:p-6"
      style={{ colorScheme: "dark" }}
    >
      <header className="mb-5 overflow-hidden rounded-[22px] border border-[#2A2E3A] bg-[#0d0f15]">
        <div className="h-1 bg-[linear-gradient(90deg,#8B5CF6_0%,#8B5CF6_55%,#FF6B4A_55%,#FF6B4A_100%)]" />
        <div className="flex flex-col gap-4 p-5 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-[#8B5CF6]">
              {eyebrow}
            </p>
            <h1 className="mt-2 text-2xl font-bold tracking-tight sm:text-[30px]">
              {title}
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[#B8BECC]">
              {description}
            </p>
            <p className="mt-3 text-xs text-[#7E8494]">
              {context.workspace.name}
              {projectName ? ` / ${projectName}` : ""}
              {context.episode ? ` / ${context.episode.title}` : ""}
            </p>
          </div>
          {actions ? (
            <div className="flex flex-wrap gap-2">{actions}</div>
          ) : null}
        </div>
      </header>
      {children}
    </main>
  );
}

export function LoadingState({
  label = "正在恢复工作区与项目上下文…",
}: {
  readonly label?: string;
}) {
  return (
    <main className="min-h-64 bg-[#07080C] p-6 text-[#B8BECC]" aria-busy="true">
      <div className="mx-auto max-w-5xl space-y-3">
        <p>{label}</p>
        <div className="h-24 animate-pulse rounded-[18px] border border-[#2A2E3A] bg-[#12141B] motion-reduce:animate-none" />
        <div className="grid gap-3 md:grid-cols-3">
          <div className="h-40 animate-pulse rounded-[18px] bg-[#12141B] motion-reduce:animate-none" />
          <div className="h-40 animate-pulse rounded-[18px] bg-[#12141B] motion-reduce:animate-none" />
          <div className="h-40 animate-pulse rounded-[18px] bg-[#12141B] motion-reduce:animate-none" />
        </div>
      </div>
    </main>
  );
}

export function StatePanel({
  title,
  detail,
  requestId,
  actionLabel,
  onAction,
  tone = "neutral",
}: {
  readonly title: string;
  readonly detail?: string;
  readonly requestId?: string;
  readonly actionLabel?: string;
  readonly onAction?: () => void;
  readonly tone?: "neutral" | "danger" | "warning" | "success";
}) {
  const toneClass =
    tone === "danger"
      ? "border-[#EF4444]/50 bg-[#2a1218]"
      : tone === "warning"
        ? "border-[#F59E0B]/45 bg-[#251b0d]"
        : tone === "success"
          ? "border-[#22C55E]/45 bg-[#0e2518]"
          : "border-[#2A2E3A] bg-[#12141B]";
  return (
    <section
      className={`rounded-[18px] border p-6 text-[#F5F6FA] ${toneClass}`}
      role={tone === "danger" ? "alert" : "status"}
    >
      <h2 className="text-lg font-semibold">{title}</h2>
      {detail ? (
        <p className="mt-2 text-sm leading-6 text-[#B8BECC]">{detail}</p>
      ) : null}
      {requestId ? (
        <p className="mt-3 font-mono text-xs text-[#7E8494]">
          请求 ID：{requestId}
        </p>
      ) : null}
      {actionLabel && onAction ? (
        <button
          type="button"
          className={`${primaryButton} mt-4`}
          onClick={onAction}
        >
          {actionLabel}
        </button>
      ) : null}
    </section>
  );
}

export function ConfirmDialog({
  open,
  title,
  detail,
  confirmLabel,
  busy = false,
  onConfirm,
  onCancel,
}: {
  readonly open: boolean;
  readonly title: string;
  readonly detail: string;
  readonly confirmLabel: string;
  readonly busy?: boolean;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-[700] grid place-items-center bg-black/70 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target && !busy) onCancel();
      }}
    >
      <section
        className="w-full max-w-lg rounded-[20px] border border-[#3A3F4E] bg-[#1E212B] p-6 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h2 className="text-xl font-semibold text-[#F5F6FA]">{title}</h2>
        <p className="mt-3 text-sm leading-6 text-[#B8BECC]">{detail}</p>
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            className={button}
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            className={primaryButton}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "提交中…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

export function BatchResultPanel({
  receipt,
  onRetry,
  busy = false,
  successTitle,
}: {
  readonly receipt: BatchReceipt;
  readonly onRetry?: (ids: readonly string[]) => void;
  readonly busy?: boolean;
  readonly successTitle?: string;
}) {
  const retryIds = failedBatchItemIds(receipt.items);
  return (
    <section
      className={`${panel} mt-4 overflow-hidden`}
      aria-label="批处理结果"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#2A2E3A] px-5 py-4">
        <div>
          <h3 className="font-semibold">
            {receipt.failed
              ? `${receipt.succeeded} 项成功，${receipt.failed} 项失败`
              : (successTitle ?? `${receipt.succeeded} 项处理成功`)}
          </h3>
          <p className="mt-1 font-mono text-xs text-[#7E8494]">
            {receipt.operationId}
          </p>
        </div>
        {retryIds.length && onRetry ? (
          <button
            type="button"
            className={button}
            disabled={busy}
            onClick={() => onRetry(retryIds)}
          >
            仅重试 {retryIds.length} 个失败项
          </button>
        ) : null}
      </div>
      <ul className="divide-y divide-[#2A2E3A]">
        {receipt.items.map((item) => (
          <li
            key={item.id}
            className="grid gap-2 px-5 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_110px_2fr]"
          >
            <code className="truncate text-[#B8BECC]">{item.id}</code>
            <span
              className={
                item.status === "succeeded"
                  ? "text-[#22C55E]"
                  : "text-[#EF4444]"
              }
            >
              {item.status === "succeeded"
                ? "成功"
                : item.status === "cancelled"
                  ? "已取消"
                  : item.status === "skipped"
                    ? "已跳过"
                    : "失败"}
            </span>
            <span className="text-[#B8BECC]">
              {item.message ?? item.code ?? "—"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function TaskSummary({
  task,
  message,
  onAdoptOutput,
  adoptLabel = "采用为资产新版本",
  busy = false,
}: {
  readonly task: TaskReceipt;
  readonly message?: string;
  readonly onAdoptOutput?: (assetId: string) => void;
  readonly adoptLabel?: string;
  readonly busy?: boolean;
}) {
  return (
    <section
      className="mt-4 rounded-[18px] border border-[#8B5CF6]/45 bg-[#1b1530] p-5"
      role="status"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold text-[#F5F6FA]">
          {message ?? "任务已创建"}
        </h3>
        <span className="rounded-full bg-[#8B5CF6]/20 px-3 py-1 text-xs text-[#c4afff]">
          {task.status}
        </span>
      </div>
      <p className="mt-2 font-mono text-xs text-[#B8BECC]">
        任务 ID：{task.taskId}
      </p>
      {task.progress !== undefined ? (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#2A2E3A]">
          <div
            className="h-full bg-[#8B5CF6]"
            style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }}
          />
        </div>
      ) : null}
      {task.failureMessage ? (
        <p className="mt-3 rounded-[10px] border border-[#EF4444]/40 bg-[#2a1218] p-3 text-sm text-[#fca5a5]">
          {task.failureMessage}
        </p>
      ) : null}
      {task.billingCurrency && task.estimatedCostMinor !== undefined ? (
        <p className="mt-3 text-xs text-[#B8BECC]">
          费用：预估 {task.estimatedCostMinor} {task.billingCurrency}
          {task.actualCostMinor !== undefined
            ? ` · 实际 ${task.actualCostMinor} ${task.billingCurrency}`
            : ""}
          {task.billingStatus ? ` · ${task.billingStatus}` : ""}
        </p>
      ) : null}
      {task.outputAssetIds?.length ? (
        <div className="mt-3">
          <p className="text-xs text-[#86efac]">
            已生成 {task.outputAssetIds.length} 个正式产物
          </p>
          <ul className="mt-2 space-y-2">
            {task.outputAssetIds.map((assetId) => (
              <li
                key={assetId}
                className="flex items-center justify-between gap-3"
              >
                <code className="truncate text-xs text-[#B8BECC]">
                  {assetId}
                </code>
                {onAdoptOutput ? (
                  <button
                    type="button"
                    className={button}
                    disabled={busy}
                    onClick={() => onAdoptOutput(assetId)}
                  >
                    {adoptLabel}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
