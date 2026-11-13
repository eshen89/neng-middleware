import { createHash } from "node:crypto";

export function schemaName(env: NodeJS.ProcessEnv = process.env): string {
  const schema = env.NENG_DB_SCHEMA ?? "public";
  if (!/^[a-z_][a-z0-9_]{0,62}$/.test(schema)) {
    throw new Error("NENG_DB_SCHEMA must be a simple lowercase PostgreSQL identifier");
  }
  if (env.NENG_DEPLOYMENT_MODE === "gateway" && env.NENG_ENV === "dev" && schema !== "neng_dev") {
    throw new Error("Hosted dev must use the isolated neng_dev schema");
  }
  return schema;
}

export async function configureSchema(
  connection: { query(sql: string, parameters: string[]): Promise<unknown> },
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  // Use the active transaction so pooled connections never retain a tenant's search path.
  await connection.query("SELECT set_config('search_path', $1, true)", [schemaName(env)]);
}

export function migrationLock(service: string, env: NodeJS.ProcessEnv = process.env): bigint {
  return createHash("sha256").update(`${service}:${schemaName(env)}`).digest().readBigInt64BE();
}
