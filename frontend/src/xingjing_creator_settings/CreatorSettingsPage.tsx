import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  PreferenceApi,
  PreferenceRecord,
  VersionHistoryRecord,
} from "./api";

const pageDefinitions = {
  "CR-022": {
    title: "封面生成",
    resource: "cover-generation",
    description: "配置目标平台、画幅、标题安全区和封面生成默认策略。",
  },
  "CR-023": {
    title: "封面标题 A/B",
    resource: "cover-title-ab",
    description: "保存封面、标题、简介和标签候选的评估规则。",
  },
  "CR-046": {
    title: "创作首页",
    resource: "creator-home",
    description: "配置创作入口、最近项目、快速入口和灵感内容偏好。",
  },
  "CR-087": {
    title: "风险申诉",
    resource: "risk-appeal",
    description: "保存申诉联系信息、证据偏好和复核通知方式。",
  },
  "CR-096": {
    title: "设置",
    resource: "settings",
    description: "管理工作区、通知、生成默认值、媒体展示和安全偏好。",
  },
  "CR-115": {
    title: "版本记录",
    resource: "version-history",
    description: "配置版本链展示、差异范围、锁定与回滚确认偏好。",
  },
} as const;

export type CreatorSettingsPageId = keyof typeof pageDefinitions;

function message(error: unknown): string {
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    Number(error.status) === 409
  ) {
    return "数据已被其他会话修改，请读取最新版本后再保存。";
  }
  if (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    Number(error.status) === 403
  ) {
    return "当前账号没有读取或修改这些设置的权限。";
  }
  return "设置服务暂时不可用，已保存的数据不会丢失。";
}

export function CreatorSettingsPage({
  pageId,
  api,
  projectId,
}: {
  pageId: CreatorSettingsPageId;
  api: PreferenceApi;
  projectId?: string;
}) {
  const definition = pageDefinitions[pageId];
  const [record, setRecord] = useState<PreferenceRecord | null>();
  const [draft, setDraft] = useState("{}");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [versions, setVersions] = useState<VersionHistoryRecord[]>();

  const load = useCallback(async () => {
    setError("");
    setRecord(undefined);
    try {
      const next = await api.get(definition.resource);
      setRecord(next);
      setDraft(JSON.stringify(next?.value ?? {}, null, 2));
      if (pageId === "CR-115" && projectId) {
        setVersions(await api.versions(projectId));
      }
    } catch (reason) {
      setError(message(reason));
      setRecord(null);
    }
  }, [api, definition.resource, pageId, projectId]);

  useEffect(() => {
    // The request-backed state machine starts when its injected API or page changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const parsed = useMemo(() => {
    try {
      const value = JSON.parse(draft) as unknown;
      return typeof value === "object" && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }, [draft]);

  const save = async () => {
    if (!parsed) return;
    setSaving(true);
    setError("");
    try {
      const saved = await api.put(
        definition.resource,
        parsed,
        record?.version ?? 0,
        crypto.randomUUID(),
      );
      setRecord(saved);
      setDraft(JSON.stringify(saved.value, null, 2));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#0b0d12] p-6 text-slate-100">
      <header className="mx-auto max-w-5xl border-b border-white/10 pb-6">
        <p className="font-mono text-xs tracking-[.18em] text-violet-300">
          {pageId} · ACCOUNT PREFERENCES
        </p>
        <h1 className="mt-2 text-3xl font-semibold">{definition.title}</h1>
        <p className="mt-2 text-sm text-slate-400">{definition.description}</p>
      </header>
      <section className="mx-auto mt-6 max-w-5xl border border-white/10 bg-white/[.03] p-5">
        {record === undefined ? (
          <p role="status">正在读取服务端设置…</p>
        ) : (
          <>
            <div className="flex flex-wrap justify-between gap-3 text-xs text-slate-400">
              <span>配置键：{definition.resource}</span>
              <span>
                版本 v{record?.version ?? 0} ·{" "}
                {record?.updatedAt ?? "尚未保存"}
              </span>
            </div>
            <label className="mt-5 block text-sm">
              结构化配置
              <textarea
                className="mt-2 min-h-80 w-full border border-white/15 bg-black/30 p-4 font-mono text-sm outline-none focus:border-violet-300"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
              />
            </label>
            {!parsed && (
              <p className="mt-3 text-sm text-rose-300">
                配置必须是合法的 JSON 对象。
              </p>
            )}
            {error && (
              <div className="mt-4 border border-rose-300/30 bg-rose-300/5 p-3 text-sm text-rose-200">
                {error}
                <button
                  className="ml-3 underline"
                  type="button"
                  onClick={() => void load()}
                >
                  读取最新版本
                </button>
              </div>
            )}
            <button
              className="mt-5 bg-violet-300 px-5 py-2.5 font-semibold text-slate-950 disabled:opacity-50"
              type="button"
              disabled={!parsed || saving}
              onClick={() => void save()}
            >
              {saving ? "正在保存…" : "保存设置"}
            </button>
          </>
        )}
      </section>
      {pageId === "CR-115" && (
        <section className="mx-auto mt-6 max-w-5xl border border-white/10 bg-white/[.03] p-5">
          <h2 className="text-xl font-semibold">项目版本链</h2>
          {!projectId ? (
            <p className="mt-3 text-sm text-amber-200">当前路由缺少项目上下文。</p>
          ) : versions === undefined ? (
            <p className="mt-3 text-sm text-slate-400">正在读取剧本、资产、分镜、时间线和成片版本…</p>
          ) : versions.length === 0 ? (
            <p className="mt-3 text-sm text-slate-400">当前项目尚无可追踪版本。</p>
          ) : (
            <ul className="mt-4 space-y-3">
              {versions.map((version) => (
                <li
                  key={`${version.kind}:${version.objectId}:${version.versionId}`}
                  className="border border-white/10 bg-black/20 p-4"
                >
                  <div className="flex flex-wrap justify-between gap-3">
                    <strong>{version.kind} · {version.objectId}</strong>
                    <span className="font-mono text-xs text-violet-200">
                      {version.versionId} / r{version.revision}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-slate-300">{version.summary}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {version.createdAt ?? "历史记录未提供时间戳"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </main>
  );
}
