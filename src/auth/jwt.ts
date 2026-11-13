import { createRemoteJWKSet, customFetch, decodeProtectedHeader, jwtVerify } from "jose";
import { phase } from "../observability/timing.js";
import { assertSafeBoot, DEV_USER, loadAuthSettings } from "./settings.js";
import type { AuthSettings } from "./settings.js";

export class AuthenticationError extends Error {
  readonly status = 401;
}

const clients = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function remoteKeys(url: string) {
  let keys = clients.get(url);
  if (!keys) {
    keys = createRemoteJWKSet(new URL(url), {
      cacheMaxAge: 300_000,
      // An unknown kid must be able to refresh the cache immediately during key rotation.
      cooldownDuration: 0,
      timeoutDuration: 30_000,
      [customFetch]: (url, options) => phase("auth_fetch", async () => {
        const response = await fetch(url, options);
        if (!response.ok) return response;
        const jwks: unknown = await response.json();
        return Response.json(jwks, { status: response.status });
      }),
    });
    clients.set(url, keys);
  }
  return keys;
}

export function resetJwksCache(): void {
  clients.clear();
}

export async function verifyToken(token: string, settings: AuthSettings): Promise<string> {
  if (!token) throw new AuthenticationError("Missing bearer token");
  let subject: unknown;
  try {
    const key = await phase("auth_lookup", () => {
      const header = decodeProtectedHeader(token);
      if (!header.kid || !header.alg || !settings.algorithms.includes(header.alg)) {
        throw new Error("Invalid JWT signing header");
      }
      return remoteKeys(settings.jwksUrl)(header);
    });
    const { payload } = await phase("auth_verify", async () => {
      const verified = await jwtVerify(token, key, {
        algorithms: [...settings.algorithms],
        issuer: settings.issuer,
        audience: settings.audience,
        requiredClaims: ["exp", "iss", "aud", "sub"],
      });
      const issuedAt = verified.payload.iat;
      if (issuedAt !== undefined && issuedAt > Math.floor(Date.now() / 1000)) {
        throw new Error("Token issued in the future");
      }
      return verified;
    });
    subject = payload.sub;
  } catch {
    throw new AuthenticationError("Invalid or expired token");
  }
  if (typeof subject !== "string" || !subject) throw new AuthenticationError("Token has no subject");
  return subject;
}

export function currentUser(authorization = "", env: NodeJS.ProcessEnv = process.env): Promise<string> {
  return phase("auth_total", async () => {
    const settings = loadAuthSettings(env);
    assertSafeBoot(settings, env);
    if (settings.disabled) return DEV_USER;
    const token = authorization.slice(0, 7).toLowerCase() === "bearer " ? authorization.slice(7).trim() : "";
    return verifyToken(token, settings);
  });
}
