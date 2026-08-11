export interface SessionCredential {
  readonly accessToken: string;
  readonly expiresAt: number;
}

export interface SessionPort {
  get(): Promise<SessionCredential | null>;
  set(credential: SessionCredential): Promise<void>;
  clear(): Promise<void>;
}

export function createMemorySessionPort(initial: SessionCredential | null = null): SessionPort {
  let current = initial;
  return {
    get() { return Promise.resolve(current); },
    set(credential) { current = { ...credential }; return Promise.resolve(); },
    clear() { current = null; return Promise.resolve(); },
  };
}
