import { createServer, type IncomingHttpHeaders, type Server } from "node:http";
import type { AddressInfo } from "node:net";

export interface RecordedRequest {
  readonly method: string;
  readonly url: string;
  readonly headers: Readonly<Record<string, string>>;
  readonly body: string | null;
}

interface QueuedResponse {
  readonly status: number;
  readonly body: unknown;
  readonly headers: Readonly<Record<string, string>>;
}

export interface ContractServer {
  readonly origin: string;
  readonly requests: readonly RecordedRequest[];
  enqueueJson(
    status: number,
    body: unknown,
    headers?: Readonly<Record<string, string>>,
  ): void;
  close(): Promise<void>;
}

function normalizeHeaders(headers: IncomingHttpHeaders): Readonly<Record<string, string>> {
  return Object.fromEntries(
    Object.entries(headers).flatMap(([name, value]) => {
      if (value === undefined) return [];
      return [[name, Array.isArray(value) ? value.join(", ") : value]];
    }),
  );
}

async function readRequestBody(request: Parameters<Parameters<typeof createServer>[0]>[0]): Promise<string | null> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return chunks.length > 0 ? Buffer.concat(chunks).toString("utf-8") : null;
}

function listen(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
}

export async function startContractServer(): Promise<ContractServer> {
  const requests: RecordedRequest[] = [];
  const responses: QueuedResponse[] = [];
  const server = createServer((request, response) => {
    void (async () => {
      requests.push({
        method: request.method ?? "",
        url: request.url ?? "",
        headers: normalizeHeaders(request.headers),
        body: await readRequestBody(request),
      });
      const queued = responses.shift() ?? {
        status: 500,
        body: { detail: { code: "UNQUEUED_TEST_RESPONSE" } },
        headers: {},
      };
      response.writeHead(queued.status, {
        "Content-Type": "application/json",
        Connection: "close",
        ...queued.headers,
      });
      response.end(JSON.stringify(queued.body));
    })().catch((cause: unknown) => {
      response.writeHead(500, { "Content-Type": "application/json", Connection: "close" });
      response.end(JSON.stringify({
        detail: {
          code: cause instanceof Error ? cause.message : "CONTRACT_SERVER_FAILURE",
        },
      }));
    });
  });

  await listen(server);
  const address = server.address() as AddressInfo;

  return {
    origin: `http://127.0.0.1:${address.port}`,
    requests,
    enqueueJson(status, body, headers = {}) {
      responses.push({ status, body, headers });
    },
    async close() {
      await new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        });
        server.closeAllConnections();
      });
    },
  };
}
