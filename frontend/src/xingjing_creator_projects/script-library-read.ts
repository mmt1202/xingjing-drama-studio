import { useEffect, useState } from "react";

export type LibraryChange = (values: Record<string, string | number | null>, replace?: boolean) => void;
export const readerPageSize = 4000;

export function positiveNumber(value: string | null) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : undefined;
}

/** Keep a completed read tied to its parameters, including across rapid navigation. */
export function useLibraryRead<T>(read: (signal: AbortSignal) => Promise<T>) {
  const [attempt, retry] = useState(0);
  const [result, setResult] = useState<{ read: typeof read; attempt: number; data?: T; failed?: true }>();
  useEffect(() => {
    const controller = new AbortController();
    void read(controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({ read, attempt, data });
    }).catch(() => {
      if (!controller.signal.aborted) setResult({ read, attempt, failed: true });
    });
    return () => controller.abort();
  }, [read, attempt]);
  const current = result?.read === read && result.attempt === attempt ? result : undefined;
  return { data: current?.data, failed: Boolean(current?.failed), retry: () => retry(n => n + 1) };
}
