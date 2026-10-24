import { useState, type FormEvent } from "react";
import { Clapperboard, FileText, Layers3, Loader2 } from "lucide-react";
import { useAutoFocus } from "@/hooks/useAutoFocus";
import { errMsg, voidPromise } from "@/utils/async";
import { Link, useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { createIdentityClient, createMemoryTokenStore, resolveLoginContext, type SessionTokens } from "@/xingjing_identity/api";
import { safeReturnPath } from "@/utils/safe-url";
import { BRAND } from "@/branding";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import "./login-page.css";

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

      const envelope = await resp.json() as { data?: SessionTokens };
      const tokens = envelope.data;
      if (!tokens || typeof tokens.accessToken !== "string" || !tokens.accessToken.trim()
        || typeof tokens.refreshToken !== "string" || !tokens.refreshToken.trim()
        || typeof tokens.accessExpiresAt !== "string") throw new Error(t("auth:login_failed"));
      const client = createIdentityClient({ tokenStore: createMemoryTokenStore(tokens) });
      const context = await resolveLoginContext(client).catch((cause: unknown) => { throw new Error(t("auth:workspace_context_failed"), { cause }); });
      login(tokens.accessToken, username, tokens.refreshToken, tokens.accessExpiresAt);
      if (!context.currentWorkspace) {
        sessionStorage.setItem("xingjing_workspace_return", safeReturnPath(new URLSearchParams(search).get("from")) ?? "/app/home");
        setLocation("/workspace-select");
        return;
      }
      // 登录成功后回跳到进入登录页前的原始地址（由 AuthGuard / 401 拦截以 ?from 传入），
      // 经 safeReturnPath 校验为站内安全路径；非法或缺失时进入当前工作区主页。
      const returnTo = safeReturnPath(new URLSearchParams(search).get("from"));
      setLocation(returnTo ?? "/app/home");
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
        throw new Error(payload?.error?.message ?? t("auth:enterprise_failed"));
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
        safeReturnPath(new URLSearchParams(search).get("from")) ?? "/app/home",
      );
      setLocation("/workspace-select");
    } catch (error) {
      setError(errMsg(error, t("auth:enterprise_failed")));
    } finally {
      setLoading(false);
    }
  };

  return (
    <main data-testid="login-page" className="xj-login">
      <div className="xj-login-layout">
        <section className="xj-login-intro" aria-labelledby="login-brand-title">
          <div className="xj-login-brand">
            <img src="/xingjing-mark.svg" alt="" aria-hidden="true" width="48" height="48" />
            <span>{BRAND.name}</span>
          </div>
          <div className="xj-login-intro-copy">
            <p className="xj-login-eyebrow">{t("auth:workspace_label")}</p>
            <h1 id="login-brand-title">{t("auth:studio_title")}<br /><span>{t("auth:studio_title_accent")}</span></h1>
            <p className="xj-login-description">{t("auth:studio_description")}</p>
          </div>
          <ul className="xj-login-workflow">
            <li><FileText size={19} aria-hidden="true" /><span>{t("auth:workflow_script")}</span></li>
            <li><Layers3 size={19} aria-hidden="true" /><span>{t("auth:workflow_assets")}</span></li>
            <li><Clapperboard size={19} aria-hidden="true" /><span>{t("auth:workflow_delivery")}</span></li>
          </ul>
        </section>

        <section className="xj-login-card" aria-labelledby="login-form-title">
          <div className="xj-login-card-heading">
            <h2 id="login-form-title">{t("auth:account_login")}</h2>
            <p>{t("auth:login_description")}</p>
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
            className="xj-login-primary"
          >
            {loading && <Loader2 aria-hidden className="h-4 w-4 motion-safe:animate-spin" />}
            {loading ? t("auth:logging_in") : t("auth:login")}
          </button>
        </form>
        <div className="xj-login-links">
          <Link href="/register">{t("auth:create_account")}</Link>
          <Link href="/password-reset">{t("auth:forgot_password")}</Link>
        </div>
        <div className="xj-login-enterprise">
          <button type="button" className="xj-login-enterprise-toggle" aria-expanded={enterpriseOpen} aria-controls="enterprise-login-form" onClick={() => setEnterpriseOpen((value) => !value)}>
            {enterpriseOpen ? t("auth:enterprise_close") : t("auth:enterprise_open")}
          </button>
          {enterpriseOpen && <form id="enterprise-login-form" className="mt-4 space-y-3" onSubmit={voidPromise(handleEnterpriseLogin)}>
            <label htmlFor="login-oidc-provider" className="sr-only">{t("auth:enterprise_provider")}</label>
            <input id="login-oidc-provider" className={INPUT_CLS} placeholder={t("auth:enterprise_provider")} required maxLength={80} value={oidcProvider} onChange={(event) => setOidcProvider(event.target.value)} />
            <label htmlFor="login-oidc-token" className="sr-only">{t("auth:enterprise_credential")}</label>
            <input id="login-oidc-token" className={INPUT_CLS} type="password" placeholder={t("auth:enterprise_credential")} required maxLength={4096} value={oidcAccessToken} onChange={(event) => setOidcAccessToken(event.target.value)} />
            <button disabled={loading} className="xj-login-primary">{loading ? t("auth:enterprise_verifying") : t("auth:enterprise_login")}</button>
          </form>}
        </div>
        </section>
      </div>
    </main>
  );
}
