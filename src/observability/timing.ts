import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";

export const requestIdHeader = "x-neng-request-id";
const uuid4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const authPhases = ["auth_total", "auth_lookup", "auth_fetch", "auth_verify"] as const;
const phases = new Set([
  ...authPhases, "backend_app", "sdk_headers", "sdk_stream",
  "db_checkout", "db_connect", "db_setup", "db_sql", "db_commit", "db_rollback",
]);
export type Phase = typeof authPhases[number] | "backend_app" | "sdk_headers" | "sdk_stream"
  | "db_checkout" | "db_connect" | "db_setup" | "db_sql" | "db_commit" | "db_rollback";

type Interval = [start: number, stop: number];
interface Metrics {
  total_ms: number;
  self_ms: number;
  count: number;
  errors: number;
}
interface Trace {
  id: string;
  enabled: boolean;
  finished?: number;
  phases: Partial<Record<Phase, Metrics>>;
}

const context = new AsyncLocalStorage<{ trace: Trace; children?: Interval[] }>();

function canonicalId(value: unknown): string | undefined {
  return typeof value === "string" && value.length === 36 && uuid4.test(value) ? value : undefined;
}

function coveredTime(children: Interval[], start: number, stop: number): number {
  let end = start;
  let covered = 0;
  for (const [left, right] of children.sort(([a], [b]) => a - b)) {
    const clipped = Math.min(right, stop);
    covered += Math.max(0, clipped - Math.max(left, end));
    end = Math.max(end, clipped);
  }
  return covered;
}

export async function phase<T>(name: Phase, work: () => T | PromiseLike<T>): Promise<T> {
  if (!phases.has(name)) throw new Error("Unknown timing phase");
  const parent = context.getStore();
  if (!parent?.trace.enabled) return work();

  const { trace } = parent;
  const children: Interval[] = [];
  const start = performance.now();
  let failed = false;
  try {
    return await context.run({ trace, children }, work);
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    const stop = Math.min(performance.now(), trace.finished ?? Infinity);
    if (stop >= start) {
      const metrics = trace.phases[name] ??= { total_ms: 0, self_ms: 0, count: 0, errors: 0 };
      const duration = stop - start;
      metrics.total_ms += duration;
      metrics.self_ms += Math.max(0, duration - coveredTime(children, start, stop));
      metrics.count++;
      metrics.errors += Number(failed);
      parent.children?.push([start, stop]);
    }
  }
}

export function correlationHeaders(headers: Record<string, string>): Record<string, string> {
  const entries = Object.entries(headers);
  const values = entries.filter(([name]) => name.toLowerCase() === requestIdHeader);
  const supplied = values.length === 1 ? canonicalId(values[0][1]) : undefined;
  return {
    ...Object.fromEntries(entries.filter(([name]) => name.toLowerCase() !== requestIdHeader)),
    [requestIdHeader]: context.getStore()?.trace.id ?? supplied ?? randomUUID(),
  };
}

interface TimingOptions {
  service: "neng-agent" | "neng-user" | "neng-devsai" | "neng-gateway";
  entryPoint: "public-http" | "internal-http";
  method?: string;
  headersDistinct?: Record<string, string[] | undefined>;
  env?: NodeJS.ProcessEnv;
  log?: (record: string) => void;
}

interface ResponseTiming {
  /** Use the registered route template. */
  setRoute(template: string): void;
  responseHeaders(status: number, headers?: Record<string, string>): Record<string, string>;
  /** Call after the transport accepts each body chunk, with final=true after successful completion. */
  bodySent(bytes: number, final: boolean): void;
}

function nativeId(env: NodeJS.ProcessEnv, values?: string[]): string | undefined {
  if (!env.AWS_LAMBDA_FUNCTION_NAME || values?.length !== 1 || Buffer.byteLength(values[0]) > 8192) return;
  try {
    return canonicalId(JSON.parse(values[0]).request_id);
  } catch {
    return;
  }
}

const round = (value: number) => Number(value.toFixed(3));

/** Run an HTTP handler in an isolated timing context. */
export async function withRequestTiming<T>(
  options: TimingOptions,
  handler: (response: ResponseTiming) => T | PromiseLike<T>,
): Promise<T> {
  const { service, entryPoint, env = process.env, log = console.log, headersDistinct = {} } = options;
  if (!["neng-agent", "neng-user", "neng-devsai", "neng-gateway"].includes(service)) throw new Error("Unknown service");
  if (entryPoint !== "public-http" && entryPoint !== "internal-http") throw new Error("Unknown entry point");

  const values = headersDistinct[requestIdHeader];
  const supplied = values?.length === 1 ? canonicalId(values[0]) : undefined;
  const trace: Trace = { id: supplied ?? randomUUID(), enabled: env.NENG_TRACE_ENABLED === "true", phases: {} };
  const spanId = randomUUID();
  const started = performance.now();
  let status = 500;
  let responseBytes = 0;
  let route = "unmatched";
  let failed = false;

  try {
    return await context.run({ trace }, () => phase("backend_app", () => handler({
      setRoute(template) { route = template; },
      responseHeaders(code, headers = {}) {
        status = code;
        const outgoing = Object.fromEntries(Object.entries(headers).filter(([name]) =>
          ![requestIdHeader, "server-timing"].includes(name.toLowerCase())));
        outgoing[requestIdHeader] = trace.id;
        const timings = authPhases.flatMap((name) => {
          const metrics = trace.phases[name];
          return metrics ? [`${name};dur=${metrics.total_ms.toFixed(3)}`] : [];
        });
        if (timings.length) outgoing["server-timing"] = timings.join(", ");
        return outgoing;
      },
      bodySent(bytes, final) {
        if (trace.finished !== undefined) return;
        responseBytes += bytes;
        if (final) trace.finished = performance.now();
      },
    })));
  } catch (error) {
    failed = true;
    throw error;
  } finally {
    if (trace.enabled) {
      const ended = performance.now();
      const complete = trace.finished !== undefined;
      const finished = trace.finished ?? ended;
      const method = options.method ?? "";
      try {
        log(JSON.stringify({
          event: "neng_request_timing", protocol_version: 1, service, entry_point: entryPoint,
          trace_id: trace.id, span_id: spanId, id_source: supplied ? "upstream-asserted" : "generated",
          native_request_id: nativeId(env, headersDistinct["x-amzn-lambda-context"]) ?? null,
          route, method: /^(GET|POST|PATCH|DELETE|PUT|HEAD|OPTIONS)$/.test(method) ? method : "unknown",
          region: env.AWS_REGION ?? "local", version: env.AWS_LAMBDA_FUNCTION_VERSION ?? "local",
          status, body_complete: complete, response_bytes: responseBytes,
          outcome: failed || !complete || status >= 400 ? "error" : "success",
          duration_ms: round(finished - started),
          duration_boundary: complete ? "node-http-final-body-send" : "app-exit-incomplete",
          post_response_ms: round(ended - finished),
          phases_ms: Object.fromEntries(Object.entries(trace.phases).map(([name, metrics]) =>
            [name, { ...metrics, total_ms: round(metrics.total_ms), self_ms: round(metrics.self_ms) }])),
        }));
      } catch {
        // Diagnostics must never alter a committed write's response.
      }
    }
  }
}
