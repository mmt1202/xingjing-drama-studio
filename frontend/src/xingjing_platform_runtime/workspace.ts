export interface WorkspaceSelection {
  readonly id: string;
  readonly version: number;
}

export interface WorkspaceContext {
  get(): WorkspaceSelection;
  switchTo(next: WorkspaceSelection): void;
  subscribe(listener: (next: WorkspaceSelection) => void): () => void;
}

export function createWorkspaceContext(initial: WorkspaceSelection): WorkspaceContext {
  let current = { ...initial };
  const listeners = new Set<(next: WorkspaceSelection) => void>();
  return {
    get: () => ({ ...current }),
    switchTo(next) {
      if (!next.id.trim()) throw new Error("workspace id is required");
      current = { ...next };
      listeners.forEach((listener) => listener({ ...current }));
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
