import { useState, type FormEvent } from "react";

import { ApiError, type IdentityClient, type RegistrationResult, type SessionTokens } from "./api";

export function AuthForm({ mode, client, onAuthenticated, onRegistered }: {
  mode: "login" | "register";
  client: IdentityClient;
  onAuthenticated?: (tokens: SessionTokens) => void;
  onRegistered?: (result: RegistrationResult) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const login = mode === "login";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (login) {
        const tokens = await client.login({ email, password, deviceName: navigator.userAgent.slice(0, 160) });
        onAuthenticated?.(tokens);
      } else {
        const result = await client.register({ email, password, displayName });
        onRegistered?.(result);
      }
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : new ApiError({ status: 0, code: "UNKNOWN", message: "操作失败，请重试", retryable: true }));
    } finally { setSubmitting(false); }
  }

  const title = login ? "登录" : "注册";
  return (
    <form aria-label={title} onSubmit={(event) => void submit(event)} className="mx-auto w-full max-w-md space-y-5 rounded-3xl border border-white/10 bg-black/20 p-8 shadow-2xl backdrop-blur-xl">
      <header><h1 className="text-2xl font-semibold text-white">{title}星镜剧创</h1><p className="mt-2 text-sm text-white/55">使用真实账号继续你的创作工作区</p></header>
      {!login && <Field label="昵称" value={displayName} onChange={setDisplayName} autoComplete="name" maxLength={120} />}
      <Field label="邮箱" value={email} onChange={setEmail} type="email" autoComplete="email" maxLength={320} />
      <Field label="密码" value={password} onChange={setPassword} type="password" autoComplete={login ? "current-password" : "new-password"} minLength={login ? 1 : 12} maxLength={128} />
      {error && <div role="alert" className="rounded-xl border border-red-400/25 bg-red-400/10 px-4 py-3 text-sm text-red-100">{error.message}{error.requestId ? <span className="mt-1 block text-xs text-red-200/60">请求 ID：{error.requestId}</span> : null}</div>}
      <button className="w-full rounded-xl bg-violet-300 px-4 py-3 font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-50" disabled={submitting} type="submit">
        {submitting ? (login ? "正在登录…" : "正在创建…") : (login ? "登录" : "创建账号")}
      </button>
    </form>
  );
}

function Field({ label, value, onChange, type = "text", ...props }: {
  label: string; value: string; onChange: (value: string) => void; type?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">) {
  return <label className="block text-sm text-white/75">{label}<input {...props} required type={type} value={value} onChange={(event) => onChange(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-3 text-white outline-none focus:border-violet-300/60" /></label>;
}
