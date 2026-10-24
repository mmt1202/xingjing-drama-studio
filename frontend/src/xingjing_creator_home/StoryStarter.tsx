import { getTokenForSession } from "@/utils/auth";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { ArrowUpRight, FileUp } from "lucide-react";
import { createCreatorProjectsApi, CreatorApiError } from "@/xingjing_creator_projects/api";
import { initialSetup } from "@/xingjing_creator_projects/setup-state";
import { homeModes, type HomePreferences } from "./HomePreferences";
import { homeButton, homeField, useHomeTranslation } from "./i18n";
import { useCreatorWorkspace, useWorkspaceNavigationGuard } from "./WorkspaceFrame";

export function StoryStarter({ defaultMode }: { defaultMode: HomePreferences["defaultMode"] }) {
  const { access, go } = useCreatorWorkspace();
  const sessionEpoch = useAuthStore((state) => state.sessionEpoch);
  const { t } = useHomeTranslation();
  const api = useMemo(() => createCreatorProjectsApi({ workspaceId: access.workspaceId, getAccessToken: () => getTokenForSession(sessionEpoch) }), [access.workspaceId, sessionEpoch]);
  const [name, setName] = useState("");
  const [story, setStory] = useState("");
  const [selectedMode, setMode] = useState<HomePreferences["defaultMode"]>();
  const mode = selectedMode ?? defaultMode;
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string>();
  const [destination, setDestination] = useState<string>();
  const file = useRef<HTMLInputElement>(null);
  const canCreate = access.permissions.includes("project.manage");
  const dirty = Boolean(name || story) && !destination;
  useWorkspaceNavigationGuard(dirty || busy || reading);
  useEffect(() => { if (destination && !busy) go(destination); }, [destination, busy, go]);
  async function start(event: FormEvent) {
    event.preventDefault(); if (busy || reading || !canCreate) return;
    if (!name.trim()) { setError("nameRequired"); return; }
    if (!story.trim()) { setError("storyRequired"); return; }
    setBusy(true); setError(undefined);
    try {
      const configuration = { ...initialSetup(access.actorId), sourceKind: "script", sourceText: story.trim(), episodeCount: mode === "series" ? 3 : 1, projectType: mode === "comic" ? "comic" : "drama", visualStyle: mode === "realistic" ? t("realistic") : "" };
      const project = await api.projectAction("new", "saveDraft", { name: name.trim(), mode: "guided", configuration });
      setDestination(`projects/${encodeURIComponent(project.id)}/guided-create`);
    } catch (problem) { setError(problem instanceof CreatorApiError && problem.status === 403 ? "forbidden" : "failed"); }
    finally { setBusy(false); }
  }
  async function readFile(selected?: File) {
    if (!selected) return;
    setReading(true); setError(undefined);
    try {
      if (selected.size > 2 * 1024 * 1024 || !/\.(txt|md)$/i.test(selected.name)) throw new Error("FILE_INVALID");
      const text = new TextDecoder("utf-8", { fatal: true }).decode(await selected.arrayBuffer());
      if (text.length > 1_000_000 || text.includes("\0")) throw new Error("FILE_INVALID");
      setStory(text); if (!name.trim()) setName(selected.name.replace(/\.[^.]+$/, "").slice(0, 255));
    } catch { setError("fileError"); }
    finally { setReading(false); if (file.current) file.current.value = ""; }
  }
  return <section className="xj-story-starter relative">
    <div className="relative mx-auto max-w-4xl">
      <p className="text-xs tracking-wide text-orange-200">{t("welcome", { name: access.userName })}</p>
      <h1 className="mt-4 max-w-3xl text-3xl font-semibold leading-tight tracking-tight sm:text-5xl">{t("hero")}</h1>
      <p className="xj-story-intro mt-4 max-w-2xl text-sm leading-7 text-slate-400">{t("heroHint")}</p>
      <form onSubmit={(event) => void start(event)} className="mt-7 rounded-2xl border border-white/15 bg-[#11141D]/90 p-4 shadow-[0_24px_80px_-48px_rgba(255,107,74,0.4)] sm:p-5">
        <fieldset disabled={busy || reading || !canCreate}>
          <label className="block text-xs text-slate-400">{t("name")}<input className={homeField + " mt-2 w-full border-transparent bg-white/[0.035]"} value={name} maxLength={255} placeholder={t("namePlaceholder")} onChange={(event) => setName(event.target.value)} required /></label>
          <label className="sr-only" htmlFor="home-story">{t("story")}</label><textarea id="home-story" value={story} maxLength={1_000_000} onChange={(event) => setStory(event.target.value)} placeholder={t("storyPlaceholder")} rows={4} className="mt-4 w-full resize-y rounded-lg bg-transparent p-2 text-sm leading-7 placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-orange-400" />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-4 border-t border-white/10 pt-4">
            <div role="group" aria-label={t("mode")} className="flex flex-wrap gap-2">{homeModes.map((item) => <button key={item} type="button" aria-pressed={mode === item} onClick={() => setMode(item)} className={`rounded-full border px-3 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400 ${mode === item ? "border-violet-300/40 bg-violet-400/15 text-violet-100" : "border-white/10 text-slate-400"}`}>{t(item)}</button>)}</div>
            <div className="flex gap-2"><input ref={file} type="file" accept=".txt,.md,text/plain,text/markdown" className="sr-only" aria-label={t("upload")} onChange={(event) => void readFile(event.target.files?.[0])} /><button type="button" title={t("fileHint")} className={homeButton} onClick={() => file.current?.click()}><FileUp size={16} />{t("upload")}</button><button className={homeButton + " xj-shell-button--primary font-medium text-white"}><ArrowUpRight size={17} />{t(busy ? "starting" : "start")}</button></div>
          </div>
        </fieldset>
        {dirty && <button type="button" className="mt-3 text-xs text-slate-400 underline underline-offset-4" disabled={busy || reading} onClick={() => { setName(""); setStory(""); setMode(undefined); setError(undefined); }}>{t("cancel")}</button>}
        {error && <p role="alert" className="mt-3 text-sm text-amber-200">{t(error)}</p>}
        {!canCreate && <p className="mt-3 text-sm text-amber-200">{t("forbidden")}</p>}
      </form>
      <p className="xj-story-note mt-3 text-xs leading-5 text-slate-500">{t("startHint")}</p>
    </div>
  </section>;
}
