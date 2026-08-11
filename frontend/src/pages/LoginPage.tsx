import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { errMsg, voidPromise } from "@/utils/async";
import { Link, useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { safeReturnPath } from "@/utils/safe-url";
import { BRAND } from "@/branding";
import { FieldLabel } from "@/components/ui/FieldLabel";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  INPUT_CLS,
  ambientGlowStyle,
  posterGridStyle,
} from "@/components/ui/darkroom-tokens";

const POSTER_GRID_STYLE = posterGridStyle({ size: 44, maskShape: "60% 60% at 50% 35%", opacity: 0.05 });
const AMBIENT_GLOW_STYLE = ambientGlowStyle();

export function LoginPage() {
  const { t, i18n } = useTranslation(["common", "auth"]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [enterpriseOpen, setEnterpriseOpen] = useState(false);
  const [oidcProvider, setOidcProvider] = useState("");
  const [oidcAccessToken, setOidcAccessToken] = useState("");
  const [, setLocation] = useLocation();
  const search = useSearch();
  const login = useAuthStore((s) => s.login);
  const usernameRef = useAutoFocus<HTMLInputElement>();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const resp = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept-Language": i18n.language || "zh",
        },
        body: JSON.stringify({
          email: username,
          password,
          deviceName: navigator.userAgent.slice(0, 160),
        }),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({})) as { error?: { message?: string } };
        throw new Error(data.error?.message ?? t("auth:login_failed"));
      }

      const envelope = await resp.json() as { data: {
        accessToken: string;
        refreshToken: string;
        accessExpiresAt: string;
      } };
      login(envelope.data.accessToken, username, envelope.data.refreshToken, envelope.data.accessExpiresAt);
      const contextResponse = await fetch("/api/v1/session/context", {
        headers: { Authorization: `Bearer ${envelope.data.accessToken}` },
      });
      if (contextResponse.ok) {
        const contextEnvelope = await contextResponse.json() as { data: {
          currentWorkspace: { id: string } | null;
          workspaces: Array<{ id: string }>;
        } };
        if (!contextEnvelope.data.currentWorkspace && contextEnvelope.data.workspaces.length !== 1) {
          sessionStorage.setItem("xingjing_workspace_return", safeReturnPath(new URLSearchParams(search).get("from")) ?? "/app/projects");
          setLocation("/workspace-select");
          return;
        }
        if (!contextEnvelope.data.currentWorkspace && contextEnvelope.data.workspaces.length === 1) {
          await fetch("/api/v1/session/context/workspace", {
            method: "PUT",
            headers: {
              Authorization: `Bearer ${envelope.data.accessToken}`,
              "Content-Type": "application/json",
              "Idempotency-Key": crypto.randomUUID(),
            },
            body: JSON.stringify({ workspaceId: contextEnvelope.data.workspaces[0]?.id }),
          });
        }
      }
      // 登录成功后回跳到进入登录页前的原始地址（由 AuthGuard / 401 拦截以 ?from 传入），
      // 经 safeReturnPath 校验为站内安全路径；非法或缺失时回退到项目列表。
      const returnTo = safeReturnPath(new URLSearchParams(search).get("from"));
      setLocation(returnTo ?? "/app/projects");
    } catch (err) {
      setError(errMsg(err, t("auth:login_failed")));
    } finally {
      setLoading(false);
    }
  };

  const handleEnterpriseLogin = async (event: FormEvent) => {
    event.preventDefault();
    if (!oidcProvider.trim() || !oidcAccessToken.trim()) return;
    setError("");
    setLoading(true);
    try {
      const response = await fetch("/api/v1/auth/oidc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: oidcProvider.trim(),
          accessToken: oidcAccessToken.trim(),
          deviceName: navigator.userAgent.slice(0, 160),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
        throw new Error(payload?.error?.message ?? "企业身份登录失败");
      }
      const envelope = await response.json() as { data: {
        accessToken: string;
        refreshToken: string;
        accessExpiresAt: string;
      } };
      login(
        envelope.data.accessToken,
        `oidc:${oidcProvider.trim()}`,
        envelope.data.refreshToken,
        envelope.data.accessExpiresAt,
      );
      sessionStorage.setItem(
        "xingjing_workspace_return",
        safeReturnPath(new URLSearchParams(search).get("from")) ?? "/app/projects",
      );
      setLocation("/workspace-select");
    } catch (error) {
      setError(errMsg(error, "企业身份登录失败"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      data-testid="login-page"
      className="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg px-4 text-text"
    >
      <div aria-hidden className="pointer-events-none absolute inset-0" style={AMBIENT_GLOW_STYLE} />
      <div aria-hidden className="pointer-events-none absolute inset-0" style={POSTER_GRID_STYLE} />

      <div
        className="relative w-full max-w-sm overflow-hidden rounded-2xl border border-hairline p-8 shadow-2xl"
        style={CARD_STYLE}
      >
        <div className="mb-6 text-center">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">
            system · login
          </div>
          <h1 className="font-editorial mt-1 flex items-center justify-center gap-2 text-[28px] tracking-tight text-text">
            <img src="/android-chrome-192x192.png" alt="" aria-hidden className="h-7 w-7" />
            <span>{BRAND.name}</span>
          </h1>
        </div>

        <form onSubmit={voidPromise(handleSubmit)} className="space-y-4">
          <div>
            <FieldLabel htmlFor="login-username" required>
              {t("auth:username")}
            </FieldLabel>
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={INPUT_CLS}
              ref={usernameRef}
              required
            />
          </div>

          <div>
            <FieldLabel htmlFor="login-password" required>
              {t("auth:password")}
            </FieldLabel>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT_CLS}
              required
            />
          </div>

          {error && (
            <p role="alert" aria-live="polite" className="text-sm text-warm-bright">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className={`${ACCENT_BTN_CLS} w-full justify-center`}
            style={ACCENT_BUTTON_STYLE}
          >
            {loading && <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />}
            {loading ? t("auth:logging_in") : t("auth:login")}
          </button>
        </form>
        <div className="mt-5 flex items-center justify-between text-sm">
          <Link href="/register" className="text-amber-200 hover:text-amber-100">创建账号</Link>
          <Link href="/password-reset" className="text-stone-400 hover:text-stone-200">忘记密码？</Link>
        </div>
        <div className="mt-5 border-t border-hairline pt-5">
          <button type="button" className="w-full rounded-lg border border-hairline px-4 py-2 text-sm text-text-2" onClick={() => setEnterpriseOpen((value) => !value)}>
            {enterpriseOpen ? "收起企业身份登录" : "使用企业身份登录"}
          </button>
          {enterpriseOpen && <form className="mt-4 space-y-3" onSubmit={voidPromise(handleEnterpriseLogin)}>
            <input className={INPUT_CLS} placeholder="OIDC 提供方标识" required maxLength={80} value={oidcProvider} onChange={(event) => setOidcProvider(event.target.value)} />
            <input className={INPUT_CLS} type="password" placeholder="企业身份访问凭证" required maxLength={4096} value={oidcAccessToken} onChange={(event) => setOidcAccessToken(event.target.value)} />
            <button disabled={loading} className={`${ACCENT_BTN_CLS} w-full justify-center`}>{loading ? "正在验证…" : "企业身份登录"}</button>
          </form>}
        </div>
      </div>
    </div>
  );
}
