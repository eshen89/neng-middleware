import { randomUUID } from "node:crypto";
import { InvokeCommand, InvokeWithResponseStreamCommand, LambdaClient } from "@aws-sdk/client-lambda";
import type { InvokeWithResponseStreamResponseEvent } from "@aws-sdk/client-lambda";
import { NodeHttpHandler } from "@smithy/node-http-handler";
import { correlationHeaders, phase } from "../observability/timing.js";

const maxMetadata = 65_536;
const delimiter = Buffer.alloc(8);
const emptyBody = Buffer.alloc(0);

export class LambdaHTTPError extends Error {}

export interface HTTPRequest {
  functionName: string;
  method: string;
  path: string;
  headers: Record<string, string>;
  body?: Uint8Array;
  query?: string;
  streaming?: boolean;
  signal?: AbortSignal;
}

export function httpEvent({ method, path, headers, body = emptyBody, query = "" }: Omit<HTTPRequest, "functionName">) {
  return {
    version: "2.0", routeKey: "$default", rawPath: path, rawQueryString: query,
    headers: correlationHeaders(headers),
    requestContext: {
      routeKey: "$default", stage: "$default", domainName: "internal.neng",
      requestId: randomUUID().replaceAll("-", ""), timeEpoch: Date.now(),
      http: { method, path, protocol: "HTTP/1.1", sourceIp: "127.0.0.1", userAgent: "neng-lambda-invoke" },
    },
    body: Buffer.from(body).toString("base64"), isBase64Encoded: true,
  };
}

type ResponseHead = Pick<HTTPResult, "status" | "headers">;

function responseHead(value: {
  statusCode?: number | string;
  headers?: Record<string, string>;
}): ResponseHead {
  const { statusCode, headers = {} } = value ?? {};
  const status = Number(statusCode);
  if (!Number.isInteger(status) || status < 100 || status > 599 ||
      !headers || typeof headers !== "object" || Array.isArray(headers) ||
      Object.values(headers).some((header) => typeof header !== "string")) {
    throw new LambdaHTTPError("Invalid internal response metadata");
  }
  return { status, headers };
}

export class HTTPResult {
  readonly chunks: AsyncIterableIterator<Buffer>;

  constructor(
    readonly status: number,
    readonly headers: Record<string, string>,
    body: AsyncGenerator<Buffer>,
    closeSource: () => Promise<unknown> = async () => {},
  ) {
    this.chunks = {
      next: () => phase("sdk_stream", () => body.next()),
      return: async () => {
        // Returning an unstarted async generator skips its finally block.
        await closeSource();
        await body.return(undefined);
        return { done: true, value: undefined };
      },
      [Symbol.asyncIterator]() { return this; },
    };
  }

  async close(): Promise<void> {
    await this.chunks.return?.();
  }

  async read(): Promise<Buffer> {
    const chunks: Buffer[] = [];
    try {
      for await (const chunk of this.chunks) chunks.push(chunk);
      return Buffer.concat(chunks);
    } finally {
      await this.close();
    }
  }

  async json(): Promise<unknown> {
    return JSON.parse((await this.read()).toString("utf8"));
  }
}

async function* payloadChunks(events: AsyncIterable<InvokeWithResponseStreamResponseEvent>) {
  let complete = false;
  for await (const event of events) {
    if (event.PayloadChunk?.Payload) yield Buffer.from(event.PayloadChunk.Payload);
    else if (event.InvokeComplete) {
      if (event.InvokeComplete.ErrorCode) throw new LambdaHTTPError("Internal response stream failed");
      complete = true;
    }
  }
  if (!complete) throw new LambdaHTTPError("Internal response stream ended without completion");
}

async function readStream(
  events: AsyncIterable<InvokeWithResponseStreamResponseEvent>,
  controller: AbortController,
): Promise<HTTPResult> {
  const chunks = payloadChunks(events);
  let prefix: Buffer<ArrayBufferLike> = emptyBody;
  try {
    while (true) {
      const next = await chunks.next();
      if (next.done) throw new LambdaHTTPError("Missing internal response metadata");
      prefix = Buffer.concat([prefix, next.value]);
      const offset = prefix.indexOf(delimiter);
      if (offset > maxMetadata || (offset < 0 && prefix.length >= maxMetadata + delimiter.length)) {
        throw new LambdaHTTPError("Invalid internal response metadata");
      }
      if (offset < 0) continue;

      const { status, headers } = responseHead(JSON.parse(prefix.subarray(0, offset).toString("utf8")));
      const first = prefix.subarray(offset + delimiter.length);
      let finished = false;
      async function closeSource() {
        // Returning the SDK's decoded iterator does not destroy its HTTP response.
        if (!finished) controller.abort();
        await chunks.return(undefined);
      }
      async function* body() {
        try {
          if (first.length) yield first;
          yield* chunks;
          finished = true;
        } finally {
          await closeSource();
        }
      }
      return new HTTPResult(status, headers, body(), closeSource);
    }
  } catch (error) {
    controller.abort();
    await chunks.return(undefined);
    throw error;
  }
}

let client: LambdaClient | undefined;

function lambdaClient(): LambdaClient {
  return client ??= new LambdaClient({
    // An invocation timeout may follow an upstream write.
    maxAttempts: 1,
    requestHandler: new NodeHttpHandler({
      connectionTimeout: 5_000, socketTimeout: 360_000, httpsAgent: { maxSockets: 32, keepAlive: true },
    }),
  });
}

export function invokeHttp(request: HTTPRequest, sdk?: Pick<LambdaClient, "send">): Promise<HTTPResult> {
  return phase("sdk_headers", async () => {
    const { functionName: FunctionName, streaming = true, signal: abortSignal } = request;
    if (!FunctionName) throw new LambdaHTTPError("Internal function is not configured");
    const Payload = Buffer.from(JSON.stringify(httpEvent(request)));
    if (Payload.length > 6 * 1024 * 1024) {
      throw new LambdaHTTPError("Serialized request exceeds Lambda's synchronous payload limit");
    }
    const transport = sdk ?? lambdaClient();
    if (streaming) {
      const controller = new AbortController();
      const signal = abortSignal ? AbortSignal.any([abortSignal, controller.signal]) : controller.signal;
      try {
        const response = await transport.send(new InvokeWithResponseStreamCommand({ FunctionName, Payload }), {
          abortSignal: signal,
        });
        if (!response.EventStream) throw new LambdaHTTPError("Missing internal response stream");
        return await readStream(response.EventStream, controller);
      } catch (error) {
        controller.abort();
        throw error;
      }
    }
    const response = await transport.send(new InvokeCommand({
      FunctionName, Payload, InvocationType: "RequestResponse",
    }), { abortSignal });
    if (response.FunctionError || !response.Payload) throw new LambdaHTTPError("Internal function failed");
    const data = JSON.parse(Buffer.from(response.Payload).toString("utf8"));
    const { status, headers } = responseHead(data);
    const content = Buffer.from(data.body ?? "", data.isBase64Encoded ? "base64" : "utf8");
    async function* body() { yield content; }
    return new HTTPResult(status, headers, body());
  });
}
