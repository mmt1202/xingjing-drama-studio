import { useEffect, useState } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { getToken } from "@/utils/auth";

export class AuthenticatedMediaError extends Error {
  constructor(readonly status: number, code: string) { super(code); }
}
interface MediaState { key: string; url?: string; error?: unknown }

export function useAuthenticatedMedia({ source, workspaceId, projectId }: { source: string; workspaceId?: string; projectId: string }) {
  const token = useAuthStore((state) => state.token);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<MediaState>();
  const key = JSON.stringify([token, workspaceId, projectId, source, revision]);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    function release() { if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = undefined; } }
    function sessionChanged() {
      if (getToken() === token) return;
      controller.abort(); release();
      setState({ key, error: new AuthenticatedMediaError(401, "MEDIA_SESSION_CHANGED") });
    }
    window.addEventListener("storage", sessionChanged);
    void Promise.resolve().then(async () => {
      if (!token || !workspaceId || !projectId || getToken() !== token) throw new AuthenticatedMediaError(401, "MEDIA_SESSION_REQUIRED");
      const url = new URL(source, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/v1/") || url.username || url.password || url.searchParams.has("token")) throw new AuthenticatedMediaError(400, "MEDIA_SOURCE_INVALID");
      const response = await fetch(url.href, { signal: controller.signal, cache: "no-store", credentials: "same-origin", redirect: "error", headers: { Authorization: `Bearer ${token}`, "X-Workspace-Id": workspaceId, "X-Project-Id": projectId, Accept: "video/*,audio/*,application/octet-stream" } });
      if (!response.ok) throw new AuthenticatedMediaError(response.status, "MEDIA_READ_FAILED");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      if (getToken() !== token) throw new AuthenticatedMediaError(401, "MEDIA_SESSION_CHANGED");
      if (!blob.size || /^(application\/json|text\/html)(?:;|$)/i.test(blob.type)) throw new AuthenticatedMediaError(502, "MEDIA_RESPONSE_INVALID");
      objectUrl = URL.createObjectURL(blob);
      setState({ key, url: objectUrl });
    }).catch((error: unknown) => { if (!controller.signal.aborted) { release(); setState({ key, error }); } });
    return () => { controller.abort(); release(); window.removeEventListener("storage", sessionChanged); };
  }, [key, token, workspaceId, projectId, source]);
  const current = state?.key === key ? state : undefined;
  return { url: current?.url, error: current?.error, loading: !current, retry: () => setRevision((value) => value + 1), decodeFailed: () => {
    if (current?.url) URL.revokeObjectURL(current.url);
    setState({ key, error: new AuthenticatedMediaError(502, "MEDIA_DECODE_FAILED") });
  } };
}
