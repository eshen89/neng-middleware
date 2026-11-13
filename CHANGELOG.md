# Changelog

## 1.0.0

- Port JWT verification, SigV4 signing, Lambda HTTP transport, PostgreSQL helpers and
  request timing to TypeScript on Node.js 24. Auth and transport functions are asynchronous.
- Export the package as ESM with TypeScript declarations. PostgreSQL advisory lock IDs use
  `bigint` to preserve the full signed 64-bit value.
- Replace the ASGI timing wrapper with a handler wrapper and explicit response callbacks.
  Timing records use `node-http-final-body-send` for a completed response.

## 0.3.0

### Added
- Optional, payload-free request and phase timing with validated correlation IDs across JWT
  verification, ASGI responses and IAM Lambda calls. Timings are disabled unless explicitly enabled.

### Changed
- Restore the released 0.2.0 Lambda transport and transaction-local Postgres helpers in the
  main package line. Keep bounded requests, disabled invocation retries and stream cleanup.
- Refuse identity bypass on hosted or gateway startup, including hosted development environments.

No deployment, JWT scope, business-write permission or prepare-only policy changes are introduced.
