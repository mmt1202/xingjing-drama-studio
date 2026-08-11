import { useCallback, useEffect, useState } from "react";
import { useLocation } from "wouter";

import { useAuthStore } from "@/stores/auth-store";
import { authenticatedFetch } from "@/utils/auth";

import type { SessionContext } from "./api";
import { StatusPanel } from "./StatusPanel";

export function WorkspaceSelectionPage() {
  const token = useAuthStore((state) => state.token);
  const logout = useAuthStore((state) => state.logout);
  const [, navigate] = useLocation();
  const [context, setContext] = useState<SessionContext>();
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [selecting, setSelecting] = useState<string>();
  const [creating, setCreating] = useState(false);
  const [teamName, setTeamName] = useState("");
  const [teamSlug, setTeamSlug] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!token) { navigate("/login"); return; }
    setState("loading");
    try {
      const response = await authenticatedFetch("/api/v1/session/context");
      if (!response.ok) throw new Error("session");
      const envelope = await response.json() as { data: SessionContext };
      setContext(envelope.data);
      setState("ready");
    } catch { setState("failed"); }
  }, [navigate, token]);

  useEffect(() => {
    let active = true;
    if (!token) { navigate("/login"); return () => { active = false; }; }
    void authenticatedFetch("/api/v1/session/context").then(async (response) => {
      if (!response.ok) throw new Error("session");
      return response.json() as Promise<{ data: SessionContext }>;
    }).then((envelope) => {
      if (active) { setContext(envelope.data); setState("ready"); }
    }).catch(() => { if (active) setState("failed"); });
    return () => { active = false; };
  }, [navigate, token]);

  if (state === "loading") return <main className="grid min-h-screen place-items-center bg-[#110f0d] text-stone-300">正在读取可访问工作区…</main>;
  if (state === "failed") return <main className="grid min-h-screen place-items-center bg-[#110f0d] p-8"><StatusPanel title="工作区加载失败" message="身份服务未能返回可信工作区上下文。" action={<button onClick={() => void load()}>重试</button>} /></main>;
  if (!context?.workspaces.length) return <main className="grid min-h-screen place-items-center bg-[#110f0d] p-8"><StatusPanel title="暂无可访问工作区" message="请联系团队管理员邀请你加入，或重新登录其他账号。" action={<button onClick={() => void logout().then(() => navigate("/login"))}>退出登录</button>} /></main>;

  async function select(workspaceId: string) {
    if (!token || selecting) return;
    setSelecting(workspaceId);
    try {
      const response = await authenticatedFetch("/api/v1/session/context/workspace", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ workspaceId }),
      });
      if (!response.ok) throw new Error("workspace");
      const destination = sessionStorage.getItem("xingjing_workspace_return") ?? "/app/projects";
      sessionStorage.removeItem("xingjing_workspace_return");
      navigate(destination);
    } catch {
      setState("failed");
    } finally { setSelecting(undefined); }
  }

  async function createTeam() {
    if (!token || creating || !teamName.trim() || !teamSlug.trim()) return;
    setCreating(true);
    setMessage("");
    try {
      const response = await authenticatedFetch("/api/v1/workspaces", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
        body: JSON.stringify({ name: teamName.trim(), slug: teamSlug.trim().toLowerCase() }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { error?: { message?: string } } | null;
        throw new Error(payload?.error?.message ?? "团队工作区创建失败");
      }
      setTeamName("");
      setTeamSlug("");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "团队工作区创建失败");
    } finally {
      setCreating(false);
    }
  }

  return <main className="min-h-screen bg-[#110f0d] px-6 py-16 text-stone-100">
    <section className="mx-auto max-w-4xl">
      <p className="text-xs uppercase tracking-[0.2em] text-amber-300/70">XINGJING WORKSPACE</p>
      <h1 className="mt-3 text-3xl font-semibold">选择工作空间</h1>
      <p className="mt-2 text-stone-400">仅展示服务端确认你具有活跃成员关系的工作区。</p>
      <ul className="mt-8 grid gap-4 md:grid-cols-2">{context.workspaces.map((workspace) => <li key={workspace.id} className="rounded-2xl border border-white/10 bg-white/[0.04] p-5">
        <div className="flex items-start justify-between gap-4"><div><h2 className="font-medium">{workspace.name}</h2><p className="mt-1 text-xs text-stone-500">{workspace.slug ?? workspace.id}</p></div><span className="rounded-full bg-amber-300/10 px-2 py-1 text-xs text-amber-200">{workspace.role}</span></div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-400">
          <span>{workspace.kind === "TEAM" ? "团队空间" : "个人空间"}</span>
          <span>·</span>
          <span>套餐：{workspace.planCode ?? "未配置"}</span>
          <span>·</span>
          <span>最近进入：{workspace.lastSelectedAt ? new Date(workspace.lastSelectedAt).toLocaleString() : "尚未进入"}</span>
        </div>
        <button disabled={Boolean(selecting)} onClick={() => void select(workspace.id)} className="mt-5 rounded-lg bg-amber-200 px-4 py-2 text-sm font-medium text-stone-950 disabled:opacity-50">{selecting === workspace.id ? "正在进入…" : "进入工作区"}</button>
      </li>)}</ul>
      <section className="mt-10 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
        <h2 className="text-lg font-medium">创建团队工作区</h2>
        <p className="mt-1 text-sm text-stone-500">创建后你将成为所有者，可邀请成员并配置角色、席位和权限。</p>
        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
          <input className="rounded-lg border border-white/10 bg-black/20 px-3 py-2" placeholder="团队名称" maxLength={160} value={teamName} onChange={(event) => setTeamName(event.target.value)} />
          <input className="rounded-lg border border-white/10 bg-black/20 px-3 py-2" placeholder="英文标识，例如 studio-a" maxLength={120} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" value={teamSlug} onChange={(event) => setTeamSlug(event.target.value)} />
          <button disabled={creating || !teamName.trim() || !teamSlug.trim()} onClick={() => void createTeam()} className="rounded-lg bg-amber-200 px-4 py-2 text-sm font-medium text-stone-950 disabled:opacity-50">{creating ? "正在创建…" : "创建团队"}</button>
        </div>
        {message && <p role="alert" className="mt-3 text-sm text-red-300">{message}</p>}
      </section>
    </section>
  </main>;
}
