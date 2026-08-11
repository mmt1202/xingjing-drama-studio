import { useCallback, useEffect, useState } from "react";

import { ComplianceApiError } from "./api";
import type {
  AuthorizationRecordInput,
  ComplianceEvidence,
  ComplianceExportPort,
  ComplianceScope,
  DeliveryRecord,
  ExportPreflight,
  FormalExportFormat,
} from "./contracts";

type LoadState =
  "loading" | "ready" | "denied" | "conflict" | "unavailable" | "failed";

const exportFormats: readonly { value: FormalExportFormat; label: string; extension: string }[] = [
  { value: "mp4", label: "正式 MP4", extension: "mp4" },
  { value: "subtitle_srt", label: "字幕 SRT", extension: "srt" },
  { value: "storyboard_csv", label: "分镜表 CSV", extension: "csv" },
  { value: "davinci_edl", label: "DaVinci EDL", extension: "edl" },
  { value: "premiere_xml", label: "Premiere XML", extension: "xml" },
  { value: "compliance_report", label: "合规报告", extension: "json" },
  { value: "cost_report", label: "成本报告", extension: "json" },
  { value: "material_package", label: "素材清单包", extension: "zip" },
  { value: "project_archive", label: "项目归档包", extension: "zip" },
  { value: "jianying_draft", label: "剪映草稿包", extension: "zip" },
  { value: "publish_package", label: "正式发布包", extension: "zip" },
];

function extensionFor(format: string | null) {
  return exportFormats.find((item) => item.value === format)?.extension ?? "bin";
}

export interface ComplianceExportPanelProps {
  readonly api: ComplianceExportPort;
  readonly scope: ComplianceScope;
  readonly pageId?: string;
}

const pageTitles: Readonly<Record<string, string>> = {
  "CR-004": "AIGC 标识配置", "CR-018": "合规阻断", "CR-019": "合规报告导出",
  "CR-020": "合规版权", "CR-021": "成本报告导出", "CR-026": "DaVinci EDL 导出",
  "CR-031": "导出中心", "CR-032": "导出格式配置", "CR-033": "导出任务详情",
  "CR-047": "真人形象风险", "CR-054": "IP 改编权", "CR-055": "剪映草稿导出",
  "CR-062": "素材来源记录", "CR-071": "平台敏感词检查", "CR-072": "Premiere XML 导出",
  "CR-080": "发布物料清单", "CR-081": "发布包详情", "CR-082": "发布平台规则",
  "CR-083": "发布包", "CR-086": "授权记录", "CR-087": "风险申诉",
};

const pageDefaultFormats: Readonly<Partial<Record<string, FormalExportFormat>>> = {
  "CR-019": "compliance_report", "CR-021": "cost_report", "CR-026": "davinci_edl",
  "CR-055": "jianying_draft", "CR-072": "premiere_xml", "CR-080": "material_package",
  "CR-081": "publish_package", "CR-083": "publish_package",
};

type PageMode = "risk" | "rights" | "export" | "history" | "release";

const pageModes: Readonly<Record<string, PageMode>> = {
  "CR-004": "risk", "CR-018": "risk", "CR-019": "export", "CR-020": "rights",
  "CR-021": "export", "CR-026": "export", "CR-031": "export", "CR-032": "export",
  "CR-033": "history", "CR-047": "risk", "CR-054": "rights", "CR-055": "export",
  "CR-062": "rights", "CR-071": "risk", "CR-072": "export", "CR-080": "release",
  "CR-081": "release", "CR-082": "release", "CR-083": "release", "CR-086": "rights",
  "CR-087": "risk",
};

const pageDescriptions: Readonly<Record<string, string>> = {
  "CR-004": "核对当前不可变成片是否满足 AIGC 标识要求；不满足时正式导出会被服务端阻断。",
  "CR-018": "查看阻断原因、审核版本和解除条件，并在同一版本上发起复检、复核或申诉。",
  "CR-019": "生成绑定项目版本、策略版本、授权与复核结论的可追溯合规报告。",
  "CR-020": "集中核对当前项目版本的版权授权完整性，并登记经过对象存储校验的证据。",
  "CR-021": "导出来自权威账务结算快照的成本报告，不接受浏览器计算或手工金额。",
  "CR-026": "从当前不可变时间线生成 DaVinci EDL，并保留来源版本与交付摘要。",
  "CR-031": "选择正式交付格式，执行服务端预检，创建并下载通过门禁的交付文件。",
  "CR-032": "为当前发布目标选择交付格式；水印、AIGC 与商用开关以服务端权威配置为准。",
  "CR-033": "追踪已完成导出的版本、格式、摘要、大小和下载入口。",
  "CR-047": "检查真人形象与肖像授权风险；风险结论绑定当前成片版本，内容变化后自动失效。",
  "CR-054": "登记并核对 IP 改编权证据、授权主体、有效期和证据摘要。",
  "CR-055": "生成包含时间线、字幕、素材引用和合规清单的剪映草稿交付包。",
  "CR-062": "查看当前交付绑定的授权编号和不可变来源版本，确保素材来源可追溯。",
  "CR-071": "使用当前策略版本复检平台敏感词；命中阻断规则时禁止正式导出。",
  "CR-072": "从当前不可变时间线生成 Premiere XML，并保留素材版本与时间码。",
  "CR-080": "生成包含时间线、分镜、字幕、授权和合规报告的发布物料包。",
  "CR-081": "查看正式发布包的版本、摘要、生成时间和可验证下载文件。",
  "CR-082": "核对发布目标对应的合规、AIGC、商用和授权规则是否全部满足。",
  "CR-083": "执行发布前预检并生成正式发布包；预检结果本身不会伪装成已交付文件。",
  "CR-086": "维护当前项目版本使用的授权证据，所有证据必须有真实对象键和 SHA-256。",
  "CR-087": "针对当前项目版本的合规阻断提交理由和证据，申诉进入服务端人工复核状态机。",
};

function stateFor(error: unknown): LoadState {
  if (!(error instanceof ComplianceApiError)) return "failed";
  if (error.status === 401 || error.status === 403) return "denied";
  if (
    error.status === 409 ||
    error.code === "COMPLIANCE_AUTHORITY_DATA_MISSING"
  )
    return "conflict";
  if (
    error.status === 503 ||
    error.code === "COMPLIANCE_RUNTIME_NOT_CONFIGURED" ||
    error.code === "COMPLIANCE_RUNTIME_UNAVAILABLE"
  )
    return "unavailable";
  return "failed";
}

function errorMessage(state: LoadState, error: unknown) {
  if (state === "denied") return "当前账号无权读取合规或导出交付记录。";
  if (state === "conflict")
    return "权威合规证据缺失或已失效；请在服务端补齐当前项目版本的证据后重试。";
  if (state === "unavailable")
    return "合规运行时尚未配置，当前不能确认合规状态或创建正式交付。";
  return error instanceof Error ? error.message : "读取合规数据失败。";
}

function authorityEntries(evidence: ComplianceEvidence) {
  return [
    ["合规结论", evidence.state.compliance_conclusion],
    ["人工审查", evidence.state.manual_review_status],
    ["不可变快照", evidence.state.snapshot_is_immutable],
    ["授权完整", evidence.state.authorizations_complete],
    ["费用结算", evidence.state.billing_settled],
    ["AIGC 标识", evidence.state.aigc_marking_satisfied],
    ["商用导出", evidence.state.commercial_export_enabled],
  ] as const;
}

function valueText(value: string | boolean | undefined) {
  if (typeof value === "boolean") return value ? "是" : "否";
  return value || "服务端未提供";
}

function DeliveryList({
  deliveries,
  onDownload,
}: {
  readonly deliveries: readonly DeliveryRecord[];
  readonly onDownload: (delivery: DeliveryRecord) => void;
}) {
  if (deliveries.length === 0)
    return (
      <p className="text-sm text-slate-500">
        暂无已完成交付记录。预检通过不代表已生成文件。
      </p>
    );
  return (
    <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
      {deliveries.map((delivery) => (
        <li
          key={`${delivery.project_id}-${delivery.request_id}-${delivery.created_at}`}
          className="p-3 text-sm text-slate-700"
        >
          <p className="font-medium">
            项目版本 {delivery.project_version} · 策略 {delivery.policy_version}
          </p>
          <p className="mt-1 break-all text-xs text-slate-500">
            清单摘要：{delivery.manifest_digest}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            记录时间：{delivery.created_at} · 请求：{delivery.request_id}
          </p>
          {delivery.download_path ? (
            <button
              type="button"
              className="mt-2 inline-block font-medium text-sky-700 underline"
              onClick={() => onDownload(delivery)}
            >
              下载正式 {delivery.format?.toUpperCase() ?? "交付文件"}
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function ComplianceExportPanel({
  api,
  scope,
  pageId = "CR-018",
}: ComplianceExportPanelProps) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [evidence, setEvidence] = useState<ComplianceEvidence>();
  const [deliveries, setDeliveries] = useState<readonly DeliveryRecord[]>([]);
  const [error, setError] = useState<unknown>();
  const [preflight, setPreflight] = useState<ExportPreflight>();
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewReason, setReviewReason] = useState("");
  const [exportBusy, setExportBusy] = useState(false);
  const [exportFormat, setExportFormat] = useState<FormalExportFormat>(pageDefaultFormats[pageId] ?? "mp4");
  const [authorizationBusy, setAuthorizationBusy] = useState(false);
  const [checkBusy, setCheckBusy] = useState(false);
  const [authorization, setAuthorization] = useState<AuthorizationRecordInput>({
    authorizationId: "",
    authorizationType: "",
    subjectId: "",
    evidenceObjectKey: "",
    evidenceSha256: "",
    validFrom: "",
  });
  const pageMode = pageModes[pageId] ?? "risk";
  const showRiskActions = pageMode === "risk";
  const showRights = pageMode === "rights";
  const showExport = pageMode === "export" || pageMode === "release";
  const showHistory = pageMode === "history" || pageMode === "export" || pageMode === "release";

  const reload = useCallback(async () => {
    setLoadState("loading");
    setError(undefined);
    setPreflight(undefined);
    try {
      const [nextEvidence, nextDeliveries] = await Promise.all([
        api.getCompliance(scope),
        api.listDeliveries(scope.projectId),
      ]);
      setEvidence(nextEvidence);
      setDeliveries(nextDeliveries);
      setLoadState("ready");
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    }
  }, [api, scope]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void reload();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [reload]);

  const runPreflight = async () => {
    setPreflightBusy(true);
    setError(undefined);
    try {
      const result = await api.preflightExport(scope);
      setPreflight(result);
      const nextDeliveries = await api.listDeliveries(scope.projectId);
      setDeliveries(nextDeliveries);
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    } finally {
      setPreflightBusy(false);
    }
  };

  const applyReview = async (action: "appeal" | "approve" | "reject") => {
    if (!evidence || !reviewReason.trim()) {
      setError(new Error("请填写本次合规处置原因。"));
      return;
    }
    setReviewBusy(true);
    setError(undefined);
    try {
      await api.applyReviewAction(scope, {
        action,
        reason: reviewReason.trim(),
        expectedVersion: evidence.reviewVersion,
      });
      setReviewReason("");
      await reload();
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    } finally {
      setReviewBusy(false);
    }
  };

  const submitExport = async () => {
    setExportBusy(true);
    setError(undefined);
    try {
      await api.submitExport(scope, exportFormat);
      const nextDeliveries = await api.listDeliveries(scope.projectId);
      setDeliveries(nextDeliveries);
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    } finally {
      setExportBusy(false);
    }
  };

  const recordAuthorization = async () => {
    if (
      [
        authorization.authorizationId,
        authorization.authorizationType,
        authorization.subjectId,
        authorization.evidenceObjectKey,
        authorization.evidenceSha256,
        authorization.validFrom,
      ].some((value) => !value.trim())
    ) {
      setError(
        new Error("请完整填写授权编号、类型、主体、证据对象、摘要和生效时间。"),
      );
      return;
    }
    setAuthorizationBusy(true);
    setError(undefined);
    try {
      await api.recordAuthorization(scope, {
        ...authorization,
        validFrom: new Date(authorization.validFrom).toISOString(),
        validUntil: authorization.validUntil
          ? new Date(authorization.validUntil).toISOString()
          : undefined,
      });
      setAuthorization({
        authorizationId: "",
        authorizationType: "",
        subjectId: "",
        evidenceObjectKey: "",
        evidenceSha256: "",
        validFrom: "",
      });
      await reload();
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    } finally {
      setAuthorizationBusy(false);
    }
  };

  const downloadDelivery = async (delivery: DeliveryRecord) => {
    try {
      const blob = await api.downloadDelivery(
        scope.projectId,
        delivery.request_id,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `xingjing-${scope.projectId}-${delivery.request_id}.${extensionFor(delivery.format)}`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(cause);
    }
  };

  const runComplianceCheck = async () => {
    setCheckBusy(true);
    setError(undefined);
    try {
      await api.runComplianceCheck(scope);
      await reload();
    } catch (cause) {
      setError(cause);
      setLoadState(stateFor(cause));
    } finally {
      setCheckBusy(false);
    }
  };

  if (loadState !== "ready" || !evidence)
    return (
      <section
        aria-live="polite"
        aria-busy={loadState === "loading"}
        className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
      >
        <h2 className="text-lg font-semibold text-slate-900">{pageTitles[pageId] ?? "合规与正式交付"}</h2>
        <p className="mt-3 text-sm text-slate-600">
          {loadState === "loading"
            ? "正在读取服务端权威合规证据与已完成交付记录…"
            : errorMessage(loadState, error)}
        </p>
        {loadState !== "loading" ? <div className="mt-4 flex flex-wrap gap-2"><button type="button" className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700" onClick={() => void reload()}>重新读取</button>{loadState === "conflict" ? <button type="button" disabled={checkBusy} className="rounded-md bg-sky-700 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400" onClick={() => void runComplianceCheck()}>{checkBusy ? "正在调用审核服务…" : "执行当前成片合规检查"}</button> : null}</div> : null}
      </section>
    );

  return (
    <section
      aria-labelledby="compliance-export-heading"
      className="rounded-xl border border-slate-200 bg-slate-50 p-5 shadow-sm"
    >
      <header className="flex flex-col gap-2 border-b border-slate-200 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xs font-semibold tracking-wide text-sky-700">
            权威证据 · 服务端只读
          </p>
          <h2
            id="compliance-export-heading"
            className="text-lg font-semibold text-slate-900"
          >
            {pageTitles[pageId] ?? "合规与正式交付"}
          </h2>
          <p className="mt-1 text-sm text-slate-600">
            项目版本 {evidence.projectVersion} · 导出目标 {evidence.target}
          </p>
          <p className="mt-2 max-w-3xl text-sm text-slate-700">
            {pageDescriptions[pageId] ?? "读取服务端权威合规证据并完成正式交付。"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {showRiskActions ? <button type="button" disabled={checkBusy} onClick={() => void runComplianceCheck()} className="rounded-md bg-sky-700 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400">{checkBusy ? "正在复检…" : "重新执行合规检查"}</button> : null}
          {showExport ? <button
            type="button"
            disabled={preflightBusy || exportBusy}
            onClick={() => void runPreflight()}
            className="rounded-md border border-slate-400 px-3 py-2 text-sm font-medium text-slate-800 disabled:cursor-not-allowed disabled:bg-slate-200"
          >
            {preflightBusy ? "正在预检…" : "执行导出前预检"}
          </button> : null}
          {showExport ? <button
            type="button"
            disabled={exportBusy || !preflight?.allowed}
            onClick={() => void submitExport()}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {exportBusy ? "正在生成正式交付…" : "生成所选正式交付"}
          </button> : null}
        </div>
      </header>
      {showExport ? <div className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
        <label className="block text-sm font-medium text-slate-800">
          正式交付格式
          <select
            value={exportFormat}
            onChange={(event) => setExportFormat(event.target.value as FormalExportFormat)}
            disabled={exportBusy}
            className="mt-1 block w-full max-w-md rounded-md border border-slate-300 bg-white px-3 py-2"
          >
            {exportFormats.map((format) => (
              <option key={format.value} value={format.value}>{format.label}</option>
            ))}
          </select>
        </label>
        <p className="mt-2 text-xs text-slate-500">
          所有格式共用同一服务端合规、授权、费用和版本门禁；字幕缺少真实文本时不会生成空文件。
        </p>
      </div> : null}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <article>
          <h3 className="font-medium text-slate-900">当前证据</h3>
          <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
            {authorityEntries(evidence).map(([label, value]) => (
              <div key={label}>
                <dt className="text-slate-500">{label}</dt>
                <dd className="font-medium text-slate-800">
                  {valueText(value)}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-slate-500">
            策略：{evidence.policy.id} / {evidence.policy.version} · 审核于{" "}
            {evidence.reviewedAt}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            授权记录：
            {evidence.authorizationIds.length
              ? evidence.authorizationIds.join("、")
              : "服务端未提供"}
          </p>
        </article>
        {showHistory ? <article>
          <h3 className="font-medium text-slate-900">已完成交付</h3>
          <div className="mt-2">
            <DeliveryList
              deliveries={deliveries}
              onDownload={(delivery) => void downloadDelivery(delivery)}
            />
          </div>
        </article> : null}
      </div>
      {showRiskActions ? <section className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="font-medium text-slate-900">人工复核与申诉</h3>
        <p className="mt-1 text-xs text-slate-500">
          当前复核版本 {evidence.reviewVersion}
          。所有处置由服务端执行权限、版本和幂等校验并记录审计。
        </p>
        <label className="mt-3 block text-sm text-slate-700">
          处置原因
          <textarea
            value={reviewReason}
            onChange={(event) => setReviewReason(event.target.value)}
            maxLength={2000}
            className="mt-1 min-h-20 w-full rounded-md border border-slate-300 px-3 py-2"
          />
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          {evidence.state.manual_review_status === "rejected" ? (
            <button
              type="button"
              disabled={reviewBusy}
              onClick={() => void applyReview("appeal")}
              className="rounded-md bg-amber-600 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400"
            >
              提交申诉
            </button>
          ) : null}
          {["pending", "appealed"].includes(
            evidence.state.manual_review_status ?? "",
          ) ? (
            <>
              <button
                type="button"
                disabled={reviewBusy}
                onClick={() => void applyReview("approve")}
                className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400"
              >
                复核通过
              </button>
              <button
                type="button"
                disabled={reviewBusy}
                onClick={() => void applyReview("reject")}
                className="rounded-md bg-rose-700 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400"
              >
                驳回并阻断
              </button>
            </>
          ) : null}
          {!["pending", "appealed", "rejected"].includes(
            evidence.state.manual_review_status ?? "",
          ) ? (
            <p className="text-sm text-slate-500">
              当前状态没有可执行的人工复核动作。
            </p>
          ) : null}
        </div>
      </section> : null}
      {showRights ? <section className="mt-4 rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="font-medium text-slate-900">登记授权证据</h3>
        <p className="mt-1 text-xs text-slate-500">
          证据文件必须先通过 M04 权利证据上传；M09 会重新校验对象键与
          SHA-256，不接受只填文字的虚假授权。
        </p>
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <AuthorizationField
            label="授权编号"
            value={authorization.authorizationId}
            onChange={(value) =>
              setAuthorization((current) => ({
                ...current,
                authorizationId: value,
              }))
            }
          />
          <AuthorizationField
            label="授权类型"
            value={authorization.authorizationType}
            onChange={(value) =>
              setAuthorization((current) => ({
                ...current,
                authorizationType: value,
              }))
            }
          />
          <AuthorizationField
            label="授权主体"
            value={authorization.subjectId}
            onChange={(value) =>
              setAuthorization((current) => ({ ...current, subjectId: value }))
            }
          />
          <AuthorizationField
            label="M04 证据对象键"
            value={authorization.evidenceObjectKey}
            onChange={(value) =>
              setAuthorization((current) => ({
                ...current,
                evidenceObjectKey: value,
              }))
            }
          />
          <AuthorizationField
            label="证据 SHA-256"
            value={authorization.evidenceSha256}
            onChange={(value) =>
              setAuthorization((current) => ({
                ...current,
                evidenceSha256: value,
              }))
            }
          />
          <label className="block text-sm text-slate-700">
            生效时间
            <input
              type="datetime-local"
              value={authorization.validFrom}
              onChange={(event) =>
                setAuthorization((current) => ({
                  ...current,
                  validFrom: event.target.value,
                }))
              }
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
          <label className="block text-sm text-slate-700">
            失效时间（可选）
            <input
              type="datetime-local"
              value={authorization.validUntil ?? ""}
              onChange={(event) =>
                setAuthorization((current) => ({
                  ...current,
                  validUntil: event.target.value || undefined,
                }))
              }
              className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2"
            />
          </label>
        </div>
        <button
          type="button"
          disabled={authorizationBusy}
          onClick={() => void recordAuthorization()}
          className="mt-4 rounded-md bg-sky-700 px-3 py-2 text-sm font-medium text-white disabled:bg-slate-400"
        >
          {authorizationBusy ? "正在校验证据并登记…" : "登记授权证据"}
        </button>
      </section> : null}
      {showExport && preflight && (
        <div
          role="status"
          className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"
        >
          <p className="font-medium">
            {preflight.allowed
              ? "预检允许继续进入后续服务端流程"
              : "预检已阻断"}
          </p>
          {preflight.blockCodes.length > 0 && (
            <p className="mt-1">阻断原因：{preflight.blockCodes.join("、")}</p>
          )}
          <p className="mt-1 text-amber-800">
            本次结果为预检（isDelivery: false），未创建导出、文件或下载链接。
          </p>
        </div>
      )}
      {Boolean(error) && (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900"
        >
          {errorMessage(stateFor(error), error)}{" "}
          <button
            type="button"
            className="ml-2 underline"
            onClick={() => void reload()}
          >
            重新读取
          </button>
        </div>
      )}
    </section>
  );
}

function AuthorizationField({
  label,
  value,
  onChange,
}: {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
}) {
  return (
    <label className="block text-sm text-slate-700">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2"
      />
    </label>
  );
}
