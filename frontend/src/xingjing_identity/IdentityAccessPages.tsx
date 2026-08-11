import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useParams, useSearch } from "wouter";

import { useAuthStore } from "@/stores/auth-store";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { authenticatedFetch } from "@/utils/auth";
import type { SessionContext } from "./api";

type ErrorEnvelope = { error?: { message?: string }; detail?: { message?: string } };

async function readError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => null) as ErrorEnvelope | null;
  return payload?.error?.message ?? payload?.detail?.message ?? fallback;
}

function AccessShell({ title, subtitle, children }: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return <main className="grid min-h-screen place-items-center bg-[#110f0d] px-5 py-12 text-stone-100">
    <section className="w-full max-w-md rounded-2xl border border-white/10 bg-white/[0.04] p-7 shadow-2xl">
      <p className="text-xs uppercase tracking-[0.18em] text-amber-300/70">XINGJING IDENTITY</p>
      <h1 className="mt-3 text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-sm leading-6 text-stone-400">{subtitle}</p>
      {children}
    </section>
  </main>;
}

export function RegisterPage() {
  const [, navigate] = useLocation();
  const [form, setForm] = useState({ displayName: "", email: "", password: "" });
  const [state, setState] = useState<"idle" | "submitting" | "done">("idle");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (state === "submitting") return;
    setState("submitting");
    setMessage("");
    try {
      const response = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!response.ok) throw new Error(await readError(response, "注册失败，请稍后重试"));
      setState("done");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "注册失败，请稍后重试");
      setState("idle");
    }
  }

  if (state === "done") return <AccessShell title="账号创建成功" subtitle="个人工作区已经建立，现在可以使用刚才的邮箱和密码登录。">
    <button className="mt-6 w-full rounded-xl bg-amber-200 px-4 py-3 font-medium text-stone-950" onClick={() => navigate("/login")}>前往登录</button>
  </AccessShell>;

  return <AccessShell title="创建星镜账号" subtitle="注册会同时创建你的个人工作区，账号资料由身份服务持久保存。">
    <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
      <label className="block text-sm text-stone-300">昵称
        <input className={`${INPUT_CLS} mt-2`} value={form.displayName} maxLength={120} required onChange={(event) => setForm({ ...form, displayName: event.target.value })} autoComplete="name" />
      </label>
      <label className="block text-sm text-stone-300">邮箱
        <input className={`${INPUT_CLS} mt-2`} value={form.email} maxLength={320} required type="email" onChange={(event) => setForm({ ...form, email: event.target.value })} autoComplete="email" />
      </label>
      <label className="block text-sm text-stone-300">密码
        <input className={`${INPUT_CLS} mt-2`} value={form.password} minLength={12} maxLength={128} required type="password" onChange={(event) => setForm({ ...form, password: event.target.value })} autoComplete="new-password" />
        <span className="mt-1 block text-xs text-stone-500">至少 12 位，请勿复用其他网站密码。</span>
      </label>
      {message && <p role="alert" className="text-sm text-red-300">{message}</p>}
      <button disabled={state === "submitting"} className="w-full rounded-xl bg-amber-200 px-4 py-3 font-medium text-stone-950 disabled:opacity-50">{state === "submitting" ? "正在创建…" : "创建账号"}</button>
    </form>
    <p className="mt-5 text-center text-sm text-stone-500">已有账号？ <Link href="/login" className="text-amber-200">返回登录</Link></p>
  </AccessShell>;
}

export function PasswordResetRequestPage() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<"idle" | "submitting" | "done">("idle");
  const [message, setMessage] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setState("submitting");
    setMessage("");
    const response = await fetch("/api/v1/auth/password-reset/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    }).catch(() => null);
    if (!response) {
      setMessage("网络连接失败，请稍后重试");
      setState("idle");
      return;
    }
    if (!response.ok) {
      setMessage(await readError(response, "暂时无法发送重置邮件"));
      setState("idle");
      return;
    }
    setState("done");
  }

  return <AccessShell title="找回密码" subtitle={state === "done" ? "如果该邮箱对应有效账号，重置说明已经发送。请检查邮箱并在 30 分钟内完成操作。" : "输入注册邮箱。为防止账号枚举，无论邮箱是否存在，成功响应都保持一致。"}>
    {state === "done" ? <Link href="/login" className="mt-6 block rounded-xl bg-amber-200 px-4 py-3 text-center font-medium text-stone-950">返回登录</Link> :
      <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm text-stone-300">注册邮箱
          <input className={`${INPUT_CLS} mt-2`} type="email" value={email} required maxLength={320} autoComplete="email" onChange={(event) => setEmail(event.target.value)} />
        </label>
        {message && <p role="alert" className="text-sm text-red-300">{message}</p>}
        <button disabled={state === "submitting"} className="w-full rounded-xl bg-amber-200 px-4 py-3 font-medium text-stone-950 disabled:opacity-50">{state === "submitting" ? "正在发送…" : "发送重置说明"}</button>
      </form>}
  </AccessShell>;
}

export function PasswordResetConfirmPage() {
  const search = useSearch();
  const [, navigate] = useLocation();
  const [token, setToken] = useState(() => new URLSearchParams(search).get("token") ?? "");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setMessage("");
    const response = await fetch("/api/v1/auth/password-reset/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, newPassword: password }),
    }).catch(() => null);
    if (!response || !response.ok) {
      setMessage(response ? await readError(response, "重置凭证无效或已过期") : "网络连接失败");
      setSubmitting(false);
      return;
    }
    navigate("/login?reset=success");
  }

  return <AccessShell title="设置新密码" subtitle="重置成功后，其他设备上的会话将全部失效，需要重新登录。">
    <form className="mt-6 space-y-4" onSubmit={(event) => void submit(event)}>
      <label className="block text-sm text-stone-300">重置凭证
        <input className={`${INPUT_CLS} mt-2`} value={token} required maxLength={512} onChange={(event) => setToken(event.target.value)} autoComplete="one-time-code" />
      </label>
      <label className="block text-sm text-stone-300">新密码
        <input className={`${INPUT_CLS} mt-2`} type="password" value={password} required minLength={12} maxLength={128} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" />
      </label>
      {message && <p role="alert" className="text-sm text-red-300">{message}</p>}
      <button disabled={submitting} className="w-full rounded-xl bg-amber-200 px-4 py-3 font-medium text-stone-950 disabled:opacity-50">{submitting ? "正在重置…" : "确认新密码"}</button>
    </form>
  </AccessShell>;
}

interface DeviceSession {
  id: string;
  deviceName: string;
  riskStatus: "NORMAL" | "SUSPICIOUS" | "BLOCKED";
  accessExpiresAt: string;
  refreshExpiresAt: string;
  revokedAt: string | null;
  lastSeenAt: string;
  createdAt: string;
  current: boolean;
}

interface SecurityEvent {
  id: string;
  requestId: string;
  result: "SUCCESS" | "FAILED";
  reason: string;
  ipAddress: string | null;
  region: string | null;
  deviceName: string;
  occurredAt: string;
}

interface ExternalIdentity {
  id: string;
  provider: string;
  subject: string;
  email: string;
  createdAt: string;
  revokedAt: string | null;
}

interface AccountCancellationImpact {
  activeMemberships: number;
  ownedTeamWorkspaces: number;
  activeSessions: number;
  activeExternalIdentities: number;
}

export function AccountSecurityPage() {
  const token = useAuthStore((state) => state.token);
  const logout = useAuthStore((state) => state.logout);
  const [location, navigate] = useLocation();
  const [sessions, setSessions] = useState<DeviceSession[]>([]);
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [eventFilters, setEventFilters] = useState({ result: "", deviceName: "", requestId: "" });
  const [nextEventPageToken, setNextEventPageToken] = useState<string | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState("");
  const [securityActionMessage, setSecurityActionMessage] = useState("");
  const [revokingOthers, setRevokingOthers] = useState(false);
  const [externalIdentities, setExternalIdentities] = useState<ExternalIdentity[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelPassword, setCancelPassword] = useState("");
  const [cancelConfirmation, setCancelConfirmation] = useState("");
  const [cancelError, setCancelError] = useState("");
  const [cancellationImpact, setCancellationImpact] = useState<AccountCancellationImpact>();
  const [cancellationImpactError, setCancellationImpactError] = useState("");
  const [accountVersion, setAccountVersion] = useState<number>();
  const [exporting, setExporting] = useState(false);
  const [oidcProvider, setOidcProvider] = useState("");
  const [oidcAccessToken, setOidcAccessToken] = useState("");
  const [identityMessage, setIdentityMessage] = useState("");
  const isDevicePage = location === "/creator/login-devices";
  const isRiskPage = location === "/creator/risk-login-alert";
  const isCancellationPage = location === "/creator/account-cancel";
  const showDevices = !isRiskPage && !isCancellationPage;
  const showRisks = !isDevicePage && !isCancellationPage;
  const showCancellation = !isDevicePage && !isRiskPage;
  const showEnterpriseIdentity = !isDevicePage && !isRiskPage && !isCancellationPage;
  const pageTitle = isDevicePage
    ? "登录设备管理"
    : isRiskPage
      ? "风险登录提醒"
      : isCancellationPage
        ? "账号注销"
        : "账号安全中心";

  const load = useCallback(async () => {
    if (!token) return;
    setState("loading");
    const [sessionResponse, eventResponse, profileResponse, identityResponse] = await Promise.all([
      authenticatedFetch("/api/v1/account/sessions"),
      authenticatedFetch("/api/v1/account/security-events"),
      authenticatedFetch("/api/v1/account/profile"),
      authenticatedFetch("/api/v1/account/oidc-bindings"),
    ]).catch(() => [null, null, null, null] as const);
    if (!sessionResponse?.ok || !eventResponse?.ok || !profileResponse?.ok || !identityResponse?.ok) { setState("failed"); return; }
    const [sessionEnvelope, eventEnvelope, profileEnvelope, identityEnvelope] = await Promise.all([
      sessionResponse.json() as Promise<{ data: DeviceSession[] }>,
      eventResponse.json() as Promise<{ data: { items: SecurityEvent[]; nextPageToken: string | null } }>,
      profileResponse.json() as Promise<{ data: { version: number } }>,
      identityResponse.json() as Promise<{ data: ExternalIdentity[] }>,
    ]);
    setSessions(sessionEnvelope.data);
    setEvents(eventEnvelope.data.items);
    setNextEventPageToken(eventEnvelope.data.nextPageToken);
    setAccountVersion(profileEnvelope.data.version);
    setExternalIdentities(identityEnvelope.data);
    setState("ready");
  }, [token]);

  useEffect(() => {
    if (!token) return;
    let active = true;
    void Promise.all([
      authenticatedFetch("/api/v1/account/sessions"),
      authenticatedFetch("/api/v1/account/security-events"),
      authenticatedFetch("/api/v1/account/profile"),
      authenticatedFetch("/api/v1/account/oidc-bindings"),
    ])
      .then(async ([sessionResponse, eventResponse, profileResponse, identityResponse]) => {
        if (!sessionResponse.ok || !eventResponse.ok || !profileResponse.ok || !identityResponse.ok) throw new Error("account security");
        return Promise.all([
          sessionResponse.json() as Promise<{ data: DeviceSession[] }>,
          eventResponse.json() as Promise<{ data: { items: SecurityEvent[]; nextPageToken: string | null } }>,
          profileResponse.json() as Promise<{ data: { version: number } }>,
          identityResponse.json() as Promise<{ data: ExternalIdentity[] }>,
        ]);
      })
      .then(([sessionEnvelope, eventEnvelope, profileEnvelope, identityEnvelope]) => {
        if (active) {
          setSessions(sessionEnvelope.data);
          setEvents(eventEnvelope.data.items);
          setNextEventPageToken(eventEnvelope.data.nextPageToken);
          setAccountVersion(profileEnvelope.data.version);
          setExternalIdentities(identityEnvelope.data);
          setState("ready");
        }
      })
      .catch(() => { if (active) setState("failed"); });
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (!token || !isCancellationPage) return;
    let active = true;
    void authenticatedFetch("/api/v1/account/cancellation-impact").then(async (response) => {
      if (!response.ok) throw new Error(await readError(response, "无法读取注销影响范围"));
      return response.json() as Promise<{ data: AccountCancellationImpact }>;
    }).then((envelope) => {
      if (active) setCancellationImpact(envelope.data);
    }).catch((error) => {
      if (active) setCancellationImpactError(error instanceof Error ? error.message : "无法读取注销影响范围");
    });
    return () => { active = false; };
  }, [isCancellationPage, token]);

  async function loadSecurityEvents(pageToken?: string, append = false) {
    if (!token || eventsLoading) return;
    setEventsLoading(true);
    setEventsError("");
    const query = new URLSearchParams({ pageSize: "25" });
    if (pageToken) query.set("pageToken", pageToken);
    if (eventFilters.result) query.set("result", eventFilters.result);
    if (eventFilters.deviceName.trim()) query.set("deviceName", eventFilters.deviceName.trim());
    if (eventFilters.requestId.trim()) query.set("requestId", eventFilters.requestId.trim());
    const response = await authenticatedFetch(`/api/v1/account/security-events?${query}`).catch(() => null);
    if (!response?.ok) {
      setEventsError(response ? await readError(response, "安全事件加载失败") : "网络连接失败");
      setEventsLoading(false);
      return;
    }
    const envelope = await response.json() as { data: { items: SecurityEvent[]; nextPageToken: string | null } };
    setEvents((current) => append ? [...current, ...envelope.data.items] : envelope.data.items);
    setNextEventPageToken(envelope.data.nextPageToken);
    setEventsLoading(false);
  }

  async function revoke(session: DeviceSession) {
    if (!token || session.revokedAt) return;
    const response = await authenticatedFetch(`/api/v1/account/sessions/${session.id}`, {
      method: "DELETE",
      headers: {
        "Idempotency-Key": `session-revoke:${session.id}`,
      },
    });
    if (!response.ok) return;
    if (session.current) {
      await logout();
      navigate("/login");
      return;
    }
    await load();
  }

  async function revokeOtherSessions() {
    if (!token || revokingOthers || !window.confirm("确认强制退出除当前设备外的全部登录会话？")) return;
    setRevokingOthers(true);
    setSecurityActionMessage("");
    const response = await authenticatedFetch("/api/v1/account/sessions/revoke-others", {
      method: "POST",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
      },
    }).catch(() => null);
    if (!response?.ok) {
      setSecurityActionMessage(response ? await readError(response, "无法撤销其他登录会话") : "网络连接失败");
      setRevokingOthers(false);
      return;
    }
    setSecurityActionMessage("其他设备的活跃会话已全部撤销，当前设备保持登录。安全操作已记录审计日志。");
    setRevokingOthers(false);
    await load();
  }

  async function cancelAccount(event: FormEvent) {
    event.preventDefault();
    if (!token || accountVersion === undefined) return;
    setCancelError("");
    const response = await authenticatedFetch("/api/v1/account/cancel", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        password: cancelPassword,
        confirmation: cancelConfirmation,
        expectedVersion: accountVersion,
      }),
    }).catch(() => null);
    if (!response?.ok) {
      setCancelError(response ? await readError(response, "账号注销失败") : "网络连接失败");
      return;
    }
    await logout();
    navigate("/login");
  }

  async function exportAccount() {
    if (!token || exporting) return;
    setExporting(true);
    const response = await authenticatedFetch("/api/v1/account/export").catch(() => null);
    if (!response?.ok) {
      setCancelError(response ? await readError(response, "账号数据导出失败") : "网络连接失败");
      setExporting(false);
      return;
    }
    const payload = await response.json() as { data: unknown };
    const blob = new Blob([JSON.stringify(payload.data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `xingjing-account-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setExporting(false);
  }

  async function bindEnterpriseIdentity(event: FormEvent) {
    event.preventDefault();
    if (!token || !oidcProvider.trim() || !oidcAccessToken.trim()) return;
    setIdentityMessage("");
    const response = await authenticatedFetch("/api/v1/account/oidc-bindings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ provider: oidcProvider.trim(), accessToken: oidcAccessToken.trim() }),
    }).catch(() => null);
    if (!response?.ok) {
      setIdentityMessage(response ? await readError(response, "企业身份绑定失败") : "网络连接失败");
      return;
    }
    setOidcAccessToken("");
    setIdentityMessage("企业身份已绑定");
    await load();
  }

  async function revokeEnterpriseIdentity(identity: ExternalIdentity) {
    if (!token || identity.revokedAt) return;
    const password = window.prompt("请输入当前账号密码以确认解绑企业身份");
    if (!password) return;
    const stepResponse = await authenticatedFetch("/api/v1/account/step-up", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ purpose: "identity.oidc.revoke", password }),
    }).catch(() => null);
    if (!stepResponse?.ok) {
      setIdentityMessage(stepResponse ? await readError(stepResponse, "身份复核失败") : "网络连接失败");
      return;
    }
    const stepEnvelope = await stepResponse.json() as { data: { token: string } };
    const response = await authenticatedFetch(`/api/v1/account/oidc-bindings/${identity.id}`, {
      method: "DELETE",
      headers: {
        "X-Step-Up-Token": stepEnvelope.data.token,
        "Idempotency-Key": `oidc-revoke:${identity.id}`,
      },
    }).catch(() => null);
    if (!response?.ok) {
      setIdentityMessage(response ? await readError(response, "企业身份解绑失败") : "网络连接失败");
      return;
    }
    setIdentityMessage("企业身份已解绑");
    await load();
  }

  return <main className="min-h-full bg-[#110f0d] px-6 py-10 text-stone-100">
    <section className="mx-auto max-w-4xl">
      <h1 className="text-3xl font-semibold">{pageTitle}</h1>
      <p className="mt-2 text-stone-400">查看当前账号的真实服务端会话；发现陌生设备时可立即使其退出。</p>
      <nav className="mt-5 flex flex-wrap gap-2 text-sm">
        <Link href="/creator/account-security" className="rounded-lg border border-white/10 px-3 py-2">安全总览</Link>
        <Link href="/creator/login-devices" className="rounded-lg border border-white/10 px-3 py-2">登录设备</Link>
        <Link href="/creator/risk-login-alert" className="rounded-lg border border-white/10 px-3 py-2">风险记录</Link>
        <Link href="/creator/account-cancel" className="rounded-lg border border-white/10 px-3 py-2">账号注销</Link>
      </nav>
      {state === "failed" && <div className="mt-6 rounded-xl border border-red-400/20 bg-red-400/10 p-4 text-red-200">设备会话加载失败。<button className="ml-3 underline" onClick={() => void load()}>重试</button></div>}
      {showDevices && <section className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-xl font-semibold">登录设备</h2><p className="mt-1 text-sm text-stone-500">发现异常登录时，可保留当前设备并立即撤销其他全部会话。</p></div><button disabled={revokingOthers || sessions.filter((session) => !session.current && !session.revokedAt).length === 0} className="rounded-lg border border-red-300/30 px-4 py-2 text-sm text-red-200 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => void revokeOtherSessions()}>{revokingOthers ? "正在撤销…" : "退出其他所有设备"}</button></div>
      {securityActionMessage && <p role="status" className="mt-4 rounded-xl border border-white/10 bg-white/[0.03] p-3 text-sm text-stone-300">{securityActionMessage}</p>}
      <ul className="mt-4 space-y-3">{sessions.map((session) => <li key={session.id} className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><div className="flex items-center gap-2"><h2 className="font-medium">{session.deviceName}</h2>{session.current && <span className="rounded-full bg-emerald-400/10 px-2 py-1 text-xs text-emerald-300">当前设备</span>}</div>
            <p className="mt-2 text-xs text-stone-500">最后活动：{new Date(session.lastSeenAt).toLocaleString()} · 创建：{new Date(session.createdAt).toLocaleString()}</p>
            <p className={`mt-2 text-xs ${session.riskStatus === "NORMAL" ? "text-stone-400" : "text-red-300"}`}>风险状态：{session.riskStatus} {session.revokedAt ? "· 已退出" : ""}</p>
          </div>
          {!session.revokedAt && <button className="rounded-lg border border-red-300/30 px-3 py-2 text-sm text-red-200" onClick={() => void revoke(session)}>{session.current ? "退出当前设备" : "强制退出"}</button>}
        </div>
      </li>)}</ul>
      {state === "ready" && sessions.length === 0 && <p className="mt-8 text-stone-400">没有可显示的设备会话。</p>}
      </section>}
      {showRisks && <section className="mt-10">
        <h2 className="text-xl font-semibold">近期登录安全事件</h2>
        <p className="mt-1 text-sm text-stone-500">安全记录由服务端按当前账号隔离，可按结果、设备和请求 ID 筛选并稳定翻页。</p>
        <form className="mt-4 grid gap-3 rounded-2xl border border-white/10 bg-white/[0.03] p-4 md:grid-cols-4" onSubmit={(event) => { event.preventDefault(); void loadSecurityEvents(); }}>
          <select className={INPUT_CLS} aria-label="登录结果" value={eventFilters.result} onChange={(event) => setEventFilters({ ...eventFilters, result: event.target.value })}>
            <option value="">全部结果</option><option value="SUCCESS">成功</option><option value="FAILED">失败</option>
          </select>
          <input className={INPUT_CLS} aria-label="设备名称" placeholder="设备名称包含" maxLength={160} value={eventFilters.deviceName} onChange={(event) => setEventFilters({ ...eventFilters, deviceName: event.target.value })} />
          <input className={INPUT_CLS} aria-label="请求 ID" placeholder="精确请求 ID" maxLength={160} value={eventFilters.requestId} onChange={(event) => setEventFilters({ ...eventFilters, requestId: event.target.value })} />
          <button disabled={eventsLoading} className="rounded-lg bg-amber-200 px-4 py-2 text-sm font-medium text-stone-950 disabled:opacity-50">{eventsLoading ? "查询中…" : "查询"}</button>
        </form>
        {eventsError && <p role="alert" className="mt-3 rounded-xl border border-red-400/20 bg-red-400/10 p-3 text-sm text-red-200">{eventsError}</p>}
        <div className="mt-4 overflow-x-auto rounded-2xl border border-white/10">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-white/[0.04] text-stone-400"><tr><th className="p-3">时间</th><th className="p-3">设备</th><th className="p-3">来源</th><th className="p-3">结果</th><th className="p-3">请求 ID</th></tr></thead>
            <tbody>{events.map((entry) => <tr key={entry.id} className="border-t border-white/10"><td className="p-3">{new Date(entry.occurredAt).toLocaleString()}</td><td className="p-3">{entry.deviceName}</td><td className="p-3">{entry.region ?? entry.ipAddress ?? "未知"}</td><td className={`p-3 ${entry.result === "SUCCESS" ? "text-emerald-300" : "text-red-300"}`}>{entry.result} · {entry.reason}</td><td className="p-3 font-mono text-xs text-stone-500">{entry.requestId}</td></tr>)}</tbody>
          </table>
        </div>
        {state === "ready" && events.length === 0 && <p className="mt-4 text-stone-500">暂无登录安全事件。</p>}
        {nextEventPageToken && <button disabled={eventsLoading} className="mt-4 rounded-lg border border-white/15 px-4 py-2 text-sm text-stone-200 disabled:opacity-50" onClick={() => void loadSecurityEvents(nextEventPageToken, true)}>{eventsLoading ? "加载中…" : "加载更多"}</button>}
      </section>}
      {showEnterpriseIdentity && <section className="mt-10 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <h2 className="text-xl font-semibold">企业身份与登录方式</h2>
        <p className="mt-1 text-sm text-stone-500">绑定的 OIDC 身份可用于企业登录；解绑需要再次验证当前账号。</p>
        <ul className="mt-4 space-y-2">{externalIdentities.map((identity) => <li key={identity.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 p-3">
          <div><p className="font-medium">{identity.provider}</p><p className="mt-1 text-xs text-stone-500">{identity.email} · {identity.revokedAt ? "已解绑" : "已启用"}</p></div>
          {!identity.revokedAt && <button className="rounded-lg border border-red-300/30 px-3 py-2 text-sm text-red-200" onClick={() => void revokeEnterpriseIdentity(identity)}>解绑</button>}
        </li>)}</ul>
        <form className="mt-5 grid gap-3 sm:grid-cols-2" onSubmit={(event) => void bindEnterpriseIdentity(event)}>
          <input className={INPUT_CLS} placeholder="OIDC 提供方标识" required maxLength={80} value={oidcProvider} onChange={(event) => setOidcProvider(event.target.value)} />
          <input className={INPUT_CLS} type="password" placeholder="企业身份访问凭证" required maxLength={4096} value={oidcAccessToken} onChange={(event) => setOidcAccessToken(event.target.value)} />
          {identityMessage && <p role="status" className="text-sm text-stone-300 sm:col-span-2">{identityMessage}</p>}
          <button className="w-fit rounded-lg bg-amber-200 px-4 py-2 text-sm font-medium text-stone-950">绑定企业身份</button>
        </form>
      </section>}
      {showCancellation && <section className="mt-12 rounded-2xl border border-red-400/20 bg-red-400/[0.06] p-5">
        <h2 className="text-lg font-semibold text-red-200">注销账号</h2>
        <p className="mt-2 text-sm leading-6 text-stone-400">注销会立即撤销全部登录会话并移除活跃工作区成员关系。业务数据按平台保留与合规规则处理，请先完成所需导出。</p>
        {cancellationImpact && <dl className="mt-4 grid gap-3 rounded-xl border border-white/10 bg-black/20 p-4 text-sm sm:grid-cols-2">
          <div><dt className="text-stone-500">将移除的活跃工作区关系</dt><dd className="mt-1 text-lg font-medium">{cancellationImpact.activeMemberships}</dd></div>
          <div><dt className="text-stone-500">将撤销的活跃登录会话</dt><dd className="mt-1 text-lg font-medium">{cancellationImpact.activeSessions}</dd></div>
          <div><dt className="text-stone-500">保留审计的企业身份绑定</dt><dd className="mt-1 text-lg font-medium">{cancellationImpact.activeExternalIdentities}</dd></div>
          <div><dt className="text-stone-500">必须先移交或归档的团队工作区</dt><dd className={`mt-1 text-lg font-medium ${cancellationImpact.ownedTeamWorkspaces > 0 ? "text-red-300" : "text-emerald-300"}`}>{cancellationImpact.ownedTeamWorkspaces}</dd></div>
        </dl>}
        {cancellationImpactError && <p role="alert" className="mt-4 text-sm text-red-300">{cancellationImpactError}</p>}
        {cancellationImpact?.ownedTeamWorkspaces ? <p className="mt-4 text-sm text-red-200">你仍是团队工作区所有者。请先移交所有权或归档这些工作区，之后才能注销账号。</p> : null}
        <button type="button" disabled={exporting} className="mt-4 mr-3 rounded-lg border border-white/15 px-4 py-2 text-sm text-stone-200 disabled:opacity-50" onClick={() => void exportAccount()}>{exporting ? "正在导出…" : "导出账号数据"}</button>
        {!cancelOpen ? <button disabled={!cancellationImpact || cancellationImpact.ownedTeamWorkspaces > 0} className="mt-4 rounded-lg border border-red-300/30 px-4 py-2 text-sm text-red-200 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => setCancelOpen(true)}>开始注销</button> :
          <form className="mt-5 grid gap-3 sm:grid-cols-2" onSubmit={(event) => void cancelAccount(event)}>
            <input className={INPUT_CLS} type="password" placeholder="当前密码" required maxLength={128} value={cancelPassword} onChange={(event) => setCancelPassword(event.target.value)} />
            <input className={INPUT_CLS} placeholder="输入 CANCEL 确认" required maxLength={16} value={cancelConfirmation} onChange={(event) => setCancelConfirmation(event.target.value)} />
            {cancelError && <p role="alert" className="sm:col-span-2 text-sm text-red-300">{cancelError}</p>}
            <div className="flex gap-3 sm:col-span-2"><button className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white">确认注销账号</button><button type="button" className="rounded-lg border border-white/10 px-4 py-2 text-sm" onClick={() => setCancelOpen(false)}>取消</button></div>
          </form>}
      </section>}
    </section>
  </main>;
}

const governancePages = [
  ["CR-107", "团队空间", "查看成员、角色、席位与工作区状态"],
  ["CR-075", "项目成员", "邀请成员并核对角色和数据范围"],
  ["CR-070", "权限矩阵", "维护服务端角色权限版本"],
  ["CR-077", "项目权限详情", "查看项目协作权限及成员关系"],
  ["CR-123", "工作区权限", "管理工作区级角色与能力"],
  ["CR-069", "操作日志", "按服务端审计记录追踪关键操作"],
  ["CR-035", "外链权限设置", "管理审片外链的可见版本与失效策略"],
] as const;

export function WorkspaceGovernanceHubPage() {
  const [, navigate] = useLocation();
  const token = useAuthStore((state) => state.token);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceVersion, setWorkspaceVersion] = useState<number>();
  const [workspaceRole, setWorkspaceRole] = useState("");
  const [failed, setFailed] = useState(false);
  const [archiveMessage, setArchiveMessage] = useState("");
  const [transferTargetUserId, setTransferTargetUserId] = useState("");
  const [transferMessage, setTransferMessage] = useState("");
  const [transferring, setTransferring] = useState(false);

  useEffect(() => {
    if (!token) return;
    let active = true;
    void authenticatedFetch("/api/v1/session/context")
      .then(async (response) => {
        if (!response.ok) throw new Error("context");
        return response.json() as Promise<{ data: SessionContext }>;
      })
      .then(({ data }) => {
        if (!active) return;
        setWorkspaceId(data.currentWorkspace?.id ?? "");
        setWorkspaceName(data.currentWorkspace?.name ?? "");
        setWorkspaceVersion(data.currentWorkspace?.version);
        setWorkspaceRole(data.currentWorkspace?.role ?? "");
      })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [token]);

  async function archiveWorkspace() {
    if (!token || !workspaceId || workspaceVersion === undefined) return;
    if (!window.confirm(`确认归档团队工作区“${workspaceName}”？所有成员都将无法继续进入。`)) return;
    setArchiveMessage("");
    const response = await authenticatedFetch(`/api/v1/workspaces/${workspaceId}/archive`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({ expectedVersion: workspaceVersion }),
    }).catch(() => null);
    if (!response?.ok) {
      setArchiveMessage(response ? await readError(response, "工作区归档失败") : "网络连接失败");
      return;
    }
    navigate("/workspace-select");
  }

  async function transferOwnership(event: FormEvent) {
    event.preventDefault();
    if (!token || !workspaceId || workspaceVersion === undefined || transferring) return;
    if (!window.confirm(`确认将团队工作区“${workspaceName}”的所有权移交给该成员？移交后你将变为管理员。`)) return;
    setTransferring(true);
    setTransferMessage("");
    const response = await authenticatedFetch(`/api/v1/workspaces/${workspaceId}/transfer-ownership`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ targetUserId: transferTargetUserId.trim(), expectedVersion: workspaceVersion }),
    }).catch(() => null);
    if (!response?.ok) {
      setTransferMessage(response ? await readError(response, "所有权移交失败") : "网络连接失败");
      setTransferring(false);
      return;
    }
    const envelope = await response.json() as { data: { role: string; version: number } };
    setWorkspaceRole(envelope.data.role);
    setWorkspaceVersion(envelope.data.version);
    setTransferTargetUserId("");
    setTransferMessage("所有权已移交。你现在是该工作区管理员，目标成员已成为所有者。权限将在下一次请求立即生效。");
    setTransferring(false);
  }

  return <main className="min-h-full bg-[#110f0d] px-6 py-10 text-stone-100">
    <section className="mx-auto max-w-5xl">
      <h1 className="text-3xl font-semibold">工作区与协作治理</h1>
      <p className="mt-2 text-stone-400">{workspaceName ? `当前工作区：${workspaceName}` : "正在读取当前工作区…"}</p>
      {failed && <p className="mt-6 rounded-xl border border-red-400/20 bg-red-400/10 p-4 text-red-200">无法读取可信工作区上下文，请重新登录或切换工作区。</p>}
      {!failed && !workspaceId && <Link href="/workspace-select" className="mt-6 inline-block rounded-lg bg-amber-200 px-4 py-2 text-stone-950">选择工作区</Link>}
      {workspaceId && <ul className="mt-8 grid gap-4 md:grid-cols-2">{governancePages.map(([id, title, description]) => <li key={id}>
        <Link href={`/app/workspaces/${workspaceId}/collaboration/${id}`} className="block rounded-2xl border border-white/10 bg-white/[0.04] p-5 transition hover:border-amber-200/40 hover:bg-white/[0.06]">
          <p className="font-mono text-xs text-amber-300/70">{id}</p>
          <h2 className="mt-2 text-lg font-medium">{title}</h2>
          <p className="mt-2 text-sm leading-6 text-stone-400">{description}</p>
        </Link>
      </li>)}</ul>}
      {workspaceId && workspaceRole === "OWNER" && <section className="mt-10 rounded-2xl border border-red-400/20 bg-red-400/[0.06] p-5">
        <h2 className="font-medium text-amber-100">移交团队工作区所有权</h2>
        <p className="mt-2 text-sm text-stone-400">填写当前工作区活跃成员的用户编号。移交成功后，目标成员成为所有者，你保留管理员权限。</p>
        <form className="mt-4 flex flex-wrap gap-3" onSubmit={(event) => void transferOwnership(event)}>
          <input className={`${INPUT_CLS} min-w-72 flex-1`} placeholder="目标成员用户编号（UUID）" required value={transferTargetUserId} onChange={(event) => setTransferTargetUserId(event.target.value)} />
          <button disabled={transferring || !transferTargetUserId.trim()} className="rounded-lg bg-amber-200 px-4 py-2 text-sm font-medium text-stone-950 disabled:opacity-40">{transferring ? "正在移交…" : "移交所有权"}</button>
        </form>
        {transferMessage && <p role="status" className="mt-3 text-sm text-stone-300">{transferMessage}</p>}
        <div className="my-6 border-t border-white/10" />
        <h2 className="font-medium text-red-200">归档团队工作区</h2>
        <p className="mt-2 text-sm text-stone-400">个人工作区不能归档。团队工作区归档后会清除所有活跃会话中的当前工作区上下文。</p>
        {archiveMessage && <p role="alert" className="mt-3 text-sm text-red-300">{archiveMessage}</p>}
        <button className="mt-4 rounded-lg border border-red-300/30 px-4 py-2 text-sm text-red-200" onClick={() => void archiveWorkspace()}>归档当前团队工作区</button>
      </section>}
    </section>
  </main>;
}

export function InvitationAcceptancePage() {
  const { invitationId = "" } = useParams<{ invitationId: string }>();
  const token = useAuthStore((state) => state.token);
  const [, navigate] = useLocation();
  const [state, setState] = useState<"idle" | "submitting" | "failed">("idle");
  const [message, setMessage] = useState("");

  async function accept() {
    if (!token || !invitationId || state === "submitting") return;
    setState("submitting");
    setMessage("");
    const response = await authenticatedFetch(`/api/v1/workspaces/invitations/${encodeURIComponent(invitationId)}/accept`, {
      method: "POST",
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
      },
    }).catch(() => null);
    if (!response?.ok) {
      setMessage(response ? await readError(response, "邀请已失效、已处理或不属于当前账号") : "网络连接失败");
      setState("failed");
      return;
    }
    navigate("/workspace-select");
  }

  return <AccessShell title="接受工作区邀请" subtitle="系统会用当前登录账号校验邀请收件人，并在服务端原子占用席位。邀请不匹配、已过期或席位不足时不会加入工作区。">
    <div className="mt-6 rounded-xl border border-white/10 bg-black/20 p-4">
      <p className="text-xs text-stone-500">邀请编号</p>
      <p className="mt-1 break-all font-mono text-sm text-stone-300">{invitationId || "无效邀请"}</p>
    </div>
    {message && <p role="alert" className="mt-4 text-sm text-red-300">{message}</p>}
    <button disabled={!invitationId || state === "submitting"} onClick={() => void accept()} className="mt-6 w-full rounded-xl bg-amber-200 px-4 py-3 font-medium text-stone-950 disabled:opacity-50">{state === "submitting" ? "正在加入…" : "确认加入工作区"}</button>
  </AccessShell>;
}
