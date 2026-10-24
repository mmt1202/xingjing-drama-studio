import { useEffect, useState } from "react";
import { useProjectNavigationBlocker } from "@/xingjing_creator_projects/ProjectFlowShell";

export function useDraftProtection(blocked: boolean) {
  const [navigationBlocked, setNavigationBlocked] = useState(false);
  useProjectNavigationBlocker(blocked);
  useEffect(() => {
    if (!blocked) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [blocked]);
  return { navigationBlocked: navigationBlocked && blocked, canNavigate: () => { setNavigationBlocked(blocked); return !blocked; } };
}
