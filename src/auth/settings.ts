export const DEV_USER = "dev-user";

export interface AuthSettings {
  readonly jwksUrl: string;
  readonly issuer: string;
  readonly audience: string;
  readonly algorithms: readonly string[];
  readonly env: string;
  readonly disabled: boolean;
}

export function truthy(value = ""): boolean {
  return /^(1|true|yes|on)$/i.test(value.trim());
}

export function loadAuthSettings(env: NodeJS.ProcessEnv = process.env): AuthSettings {
  const jwksUrl = env.SUPABASE_JWKS_URL?.trim() ?? "";
  return {
    jwksUrl,
    issuer: env.SUPABASE_JWT_ISSUER?.trim() ?? "",
    audience: (env.SUPABASE_JWT_AUDIENCE ?? "authenticated").trim(),
    algorithms: (env.NENG_AUTH_ALGORITHMS ?? "ES256,RS256").split(",").map((name) => name.trim()).filter(Boolean),
    env: (env.NENG_ENV ?? "dev").trim().toLowerCase(),
    disabled: truthy(env.NENG_AUTH_DISABLED) || !jwksUrl,
  };
}

export function assertSafeBoot(
  settings: AuthSettings = loadAuthSettings(),
  env: NodeJS.ProcessEnv = process.env,
): void {
  const hosted = env.AWS_LAMBDA_FUNCTION_NAME || env.NENG_DEPLOYMENT_MODE === "gateway";
  if ((settings.env === "prod" || hosted) &&
      (settings.disabled || !settings.jwksUrl || !settings.issuer || !settings.audience)) {
    throw new Error(
      "Hosted, gateway and prod environments cannot use the auth bypass or incomplete JWT configuration. " +
      "Set SUPABASE_JWKS_URL, SUPABASE_JWT_ISSUER and SUPABASE_JWT_AUDIENCE, and clear NENG_AUTH_DISABLED.",
    );
  }
}
