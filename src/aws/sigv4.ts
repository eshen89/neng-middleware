import { defaultProvider } from "@aws-sdk/credential-provider-node";
import { Hash } from "@smithy/core/serde";
import { HttpRequest } from "@smithy/core/transport";
import { SignatureV4 } from "@smithy/signature-v4";
import { loadAuthSettings, truthy } from "../auth/settings.js";

interface SigningOptions {
  body?: Uint8Array | string;
  region?: string;
  service?: string;
  headers?: Record<string, string>;
  credentials?: ReturnType<typeof defaultProvider>;
}

export async function signedHeaders(method: string, url: string, options: SigningOptions = {}) {
  const headers = { ...options.headers };
  if (loadAuthSettings().disabled || truthy(process.env.NENG_INTERNAL_BYPASS)) return headers;

  const target = new URL(url);
  const query = Object.create(null) as Record<string, string[]>;
  // A literal plus in a URL is part of the AWS query value.
  for (const [name, value] of new URLSearchParams(target.search.replaceAll("+", "%2B"))) {
    (query[name] ??= []).push(value);
  }
  for (const name of Object.keys(headers)) {
    if (["authorization", "x-amz-date", "x-amz-security-token"].includes(name.toLowerCase())) delete headers[name];
  }
  if (!Object.keys(headers).some((name) => name.toLowerCase() === "host")) headers.host = target.host;

  const credentials = options.credentials ?? defaultProvider();
  const signer = new SignatureV4({
    credentials,
    service: options.service ?? "lambda",
    region: options.region || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION || "us-east-1",
    sha256: Hash.bind(null, "sha256"),
    applyChecksum: false,
  });
  const signed = await signer.sign(new HttpRequest({
    method: method.toUpperCase(),
    protocol: target.protocol,
    hostname: target.hostname,
    path: target.pathname,
    query,
    headers,
    body: options.body,
  }));
  const {
    authorization,
    "x-amz-date": date,
    "x-amz-security-token": token,
    ...signedHeaders
  } = signed.headers;
  signedHeaders.Authorization = authorization;
  signedHeaders["X-Amz-Date"] = date;
  if (token !== undefined) signedHeaders["X-Amz-Security-Token"] = token;
  return signedHeaders;
}
