export interface SseEvent<T = unknown> {
  readonly id: string | null;
  readonly type: string;
  readonly data: T;
}

export interface SseConnectOptions {
  readonly signal?: AbortSignal;
  readonly headers?: HeadersInit;
  readonly lastEventId?: string;
  readonly onEvent: (event: SseEvent) => void;
  readonly onError?: (error: unknown) => void;
}

export interface SseClientOptions {
  readonly fetcher?: typeof fetch;
  readonly minRetryMs?: number;
  readonly maxRetryMs?: number;
  readonly random?: () => number;
  readonly dedupeWindow?: number;
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });
}

function decodeEvent(block: string): SseEvent | null {
  let id: string | null = null;
  let type = "message";
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (data.length === 0) return null;
  const raw = data.join("\n");
  try { return { id, type, data: JSON.parse(raw) as unknown }; } catch { return { id, type, data: raw }; }
}

export function createSseClient(options: SseClientOptions = {}) {
  const fetcher = options.fetcher ?? fetch;
  const minRetry = options.minRetryMs ?? 1_000;
  const maxRetry = options.maxRetryMs ?? 30_000;
  const random = options.random ?? Math.random;
  const dedupeWindow = Math.max(1, options.dedupeWindow ?? 1_000);
  return {
    connect(url: string, connectOptions: SseConnectOptions) {
      const controller = new AbortController();
      connectOptions.signal?.addEventListener("abort", () => controller.abort(), { once: true });
      void (async () => {
        let cursor = connectOptions.lastEventId;
        let attempt = 0;
        const seen = new Set<string>();
        while (!controller.signal.aborted) {
          try {
            const headers = new Headers(connectOptions.headers);
            headers.set("Accept", "text/event-stream");
            if (cursor) headers.set("Last-Event-ID", cursor);
            const response = await fetcher(url, { headers, signal: controller.signal });
            if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            while (!controller.signal.aborted) {
              const chunk = await reader.read();
              buffer += decoder.decode(chunk.value, { stream: !chunk.done });
              const blocks = buffer.split(/\r?\n\r?\n/);
              buffer = blocks.pop() ?? "";
              for (const block of blocks) {
                const event = decodeEvent(block);
                if (!event) continue;
                if (event.id) {
                  cursor = event.id;
                  if (seen.has(event.id)) continue;
                  seen.add(event.id);
                  if (seen.size > dedupeWindow) {
                    const oldest = seen.values().next().value;
                    if (oldest !== undefined) seen.delete(oldest);
                  }
                }
                connectOptions.onEvent(event);
              }
              if (chunk.done) break;
            }
            attempt = 0;
          } catch (error) {
            if (!controller.signal.aborted) connectOptions.onError?.(error);
          }
          if (!controller.signal.aborted) {
            const cap = Math.min(maxRetry, minRetry * 2 ** attempt++);
            await delay(Math.floor(cap * (0.5 + random() * 0.5)), controller.signal);
          }
        }
      })();
      return { close: () => controller.abort() };
    },
  };
}
